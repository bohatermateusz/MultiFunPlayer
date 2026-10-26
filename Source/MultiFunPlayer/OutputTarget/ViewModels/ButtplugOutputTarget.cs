using Buttplug;
using Buttplug.NewtonsoftJson;
using MultiFunPlayer.Common;
using MultiFunPlayer.Property;
using MultiFunPlayer.Shortcut;
using MultiFunPlayer.UI;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NLog;
using PropertyChanged;
using Stylet;
using System.ComponentModel;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Windows;

namespace MultiFunPlayer.OutputTarget.ViewModels;

[DisplayName("Buttplug.io")]
internal sealed class ButtplugOutputTarget : AsyncAbstractOutputTarget
{
    private SemaphoreSlim _scanSemaphore;

    public override ConnectionStatus Status { get; protected set; }
    public bool IsConnected => Status == ConnectionStatus.Connected;
    public bool IsDisconnected => Status == ConnectionStatus.Disconnected;
    public bool IsConnectBusy => Status is ConnectionStatus.Connecting or ConnectionStatus.Disconnecting;
    public bool CanToggleConnect => !IsConnectBusy;

    public EndPoint Endpoint { get; set; } = new IPEndPoint(IPAddress.Loopback, 12345);
    public ObservableConcurrentCollection<ButtplugDevice> AvailableDevices { get; }

    [DependsOn(nameof(SelectedDevice))]
    public IReadOnlyCollection<ActuatorType> AvailableActuatorTypes
        => SelectedDevice != null ? [.. SelectedDevice.Actuators.Select(a => a.ActuatorType).Distinct()] : null;

    [DependsOn(nameof(SelectedDevice), nameof(AvailableActuatorTypes))]
    public IReadOnlyCollection<ButtplugActuatorChoice> AvailableActuatorIndices
    {
        get
        {
            if (SelectedDevice == null || SelectedActuatorType == null)
                return null;

            var actuators = SelectedDevice.GetActuators(SelectedActuatorType.Value);
            var usedIndices = GetSettingsForDevice(SelectedDevice).Where(s => s.ActuatorType == SelectedActuatorType.Value).Select(s => s.ActuatorIndex).ToHashSet();
            var allowedChoices = actuators.Where(a => !usedIndices.Contains(a.Index))
                                          .GroupBy(a => a.Index)
                                          .Select(g => new ButtplugActuatorChoice(g.Key, g.First().FeatureDescriptor))
                                          .ToList();
            return allowedChoices.Count == 0 ? null : allowedChoices;
        }
    }

    public ButtplugDevice SelectedDevice { get; set; }
    public DeviceAxis SelectedDeviceAxis { get; set; }
    public ActuatorType? SelectedActuatorType { get; set; }
    public uint? SelectedActuatorIndex { get; set; }
    public bool CanAddSelected => SelectedDevice != null && SelectedDeviceAxis != null && SelectedActuatorType != null && SelectedActuatorIndex != null;

    // Quick controls for devices that expose the HISMITH S2 style "Run mode" and "Preset mode"
    // Position actuators (see the Buttplug fork). Values are sent when changed; the machine's
    // current state is not read back.
    private const string RunModeDescription = "Run mode";
    private const string PresetModeDescription = "Preset mode";
    private const int PresetModeMax = 8;
    private bool _hismithManualMode;
    private int _hismithPresetMode;

    public IReadOnlyList<int> HismithPresetModes { get; } = [.. Enumerable.Range(0, PresetModeMax + 1)];
    public bool HasHismithControls => GetQuickActuators(RunModeDescription).Any() || GetQuickActuators(PresetModeDescription).Any();

    public bool HismithManualMode
    {
        get => _hismithManualMode;
        set
        {
            if (SetAndNotify(ref _hismithManualMode, value))
                _ = SendQuickActuatorsAsync(RunModeDescription, value ? 1 : 0, 1);
        }
    }

    public int HismithPresetMode
    {
        get => _hismithPresetMode;
        set
        {
            value = Math.Clamp(value, 0, PresetModeMax);
            if (SetAndNotify(ref _hismithPresetMode, value))
                _ = SendQuickActuatorsAsync(PresetModeDescription, value, PresetModeMax);
        }
    }

    // Hismith Lube Launcher (PiuPiu): any value above 0 on its "Spray" Position actuator is one
    // squirt. The automatic mode squirts after a random number of seconds between min and max,
    // like the random timer of the official app.
    private const string SprayDescription = "Spray";
    private int _lubeAutoMinSeconds = 5;
    private int _lubeAutoMaxSeconds = 15;
    private bool _lubeAutoSpray;
    private CancellationTokenSource _lubeAutoCancellationSource;

    public bool HasLubeLauncherControls => GetQuickActuators(SprayDescription).Any();

    public int LubeAutoMinSeconds
    {
        get => _lubeAutoMinSeconds;
        set
        {
            value = Math.Clamp(value, 1, 3600);
            if (SetAndNotify(ref _lubeAutoMinSeconds, value) && _lubeAutoMaxSeconds < value)
                LubeAutoMaxSeconds = value;
        }
    }

    public int LubeAutoMaxSeconds
    {
        get => _lubeAutoMaxSeconds;
        set
        {
            value = Math.Clamp(value, 1, 3600);
            if (SetAndNotify(ref _lubeAutoMaxSeconds, value) && _lubeAutoMinSeconds > value)
                LubeAutoMinSeconds = value;
        }
    }

    public bool LubeAutoSpray
    {
        get => _lubeAutoSpray;
        set
        {
            if (!SetAndNotify(ref _lubeAutoSpray, value))
                return;

            _remoteCommandSource?.Cancel();
        _remoteCommandSource?.Dispose();
        _remoteCommandSource = null;

        _hismithSequenceSource?.Cancel();
        _hismithSequenceSource?.Dispose();
        _hismithSequenceSource = null;

        _lubeAutoCancellationSource?.Cancel();
            _lubeAutoCancellationSource?.Dispose();
            _lubeAutoCancellationSource = null;

            if (value)
            {
                _lubeAutoCancellationSource = new CancellationTokenSource();
                _ = RunLubeAutoSprayAsync(_lubeAutoCancellationSource.Token);
            }
        }
    }

    public void FireLubeLauncher()
        => _ = SendQuickActuatorsAsync(SprayDescription, 1, 1);

    private async Task RunLubeAutoSprayAsync(CancellationToken token)
    {
        try
        {
            while (!token.IsCancellationRequested)
            {
                var seconds = Random.Shared.Next(Math.Min(_lubeAutoMinSeconds, _lubeAutoMaxSeconds), Math.Max(_lubeAutoMinSeconds, _lubeAutoMaxSeconds) + 1);
                await Task.Delay(TimeSpan.FromSeconds(seconds), token);
                await SendQuickActuatorsAsync(SprayDescription, 1, 1);
            }
        }
        catch (OperationCanceledException) { }
    }

    // Speed of the machine's auto mode (the "Fucking Machine Oscillation Speed" actuator, 0-100). In manual
    // mode the machine follows the position instead.
    private const string SpeedDescription = "Fucking Machine Oscillation Speed";
    private int _hismithSpeed;

    public int HismithSpeed
    {
        get => _hismithSpeed;
        set
        {
            value = Math.Clamp(value, 0, 100);
            if (SetAndNotify(ref _hismithSpeed, value))
                _ = SendQuickActuatorsAsync(SpeedDescription, value, 100, ActuatorType.Oscillate);
        }
    }

    // Remote commands: a local UDP listener (127.0.0.1 only, off by default) that runs a few named
    // actions, used by the voice control tool. Message: {"action": "<name>", "args": [<optional value>]}.
    private IShortcutManager _shortcutManager;
    private CancellationTokenSource _remoteCommandSource;
    private bool _remoteCommandsEnabled;
    private int _remoteCommandsPort = 23555;

    public bool RemoteCommandsEnabled
    {
        get => _remoteCommandsEnabled;
        set
        {
            if (SetAndNotify(ref _remoteCommandsEnabled, value))
                RestartRemoteCommandListener();
        }
    }

    public int RemoteCommandsPort
    {
        get => _remoteCommandsPort;
        set
        {
            if (SetAndNotify(ref _remoteCommandsPort, Math.Clamp(value, 1024, 65535)) && _remoteCommandsEnabled)
                RestartRemoteCommandListener();
        }
    }

    private bool IsRemoteActionAllowed(string name)
        => !string.IsNullOrWhiteSpace(name)
        && (name.StartsWith($"{Identifier}::Hismith::", StringComparison.Ordinal)
         || name.StartsWith("Script::Loop::", StringComparison.Ordinal)
         || name.StartsWith("Media::PlayPause::", StringComparison.Ordinal));

    private void RestartRemoteCommandListener()
    {
        _remoteCommandSource?.Cancel();
        _remoteCommandSource?.Dispose();
        _remoteCommandSource = null;

        if (!_remoteCommandsEnabled)
            return;

        _remoteCommandSource = new CancellationTokenSource();
        _ = RunRemoteCommandListenerAsync(_remoteCommandsPort, _remoteCommandSource.Token);
    }

    private async Task RunRemoteCommandListenerAsync(int port, CancellationToken token)
    {
        try
        {
            using var client = new UdpClient(new IPEndPoint(IPAddress.Loopback, port));
            Logger.Info("Listening for remote commands on 127.0.0.1:{0}", port);

            while (!token.IsCancellationRequested)
            {
                var result = await client.ReceiveAsync(token);
                try
                {
                    await HandleRemoteCommandAsync(Encoding.UTF8.GetString(result.Buffer));
                }
                catch (Exception e)
                {
                    Logger.Warn(e, "Failed to handle remote command");
                }
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception e)
        {
            Logger.Error(e, "Remote command listener failed [Port: {0}]", port);
        }
    }

    private async Task HandleRemoteCommandAsync(string message)
    {
        var json = JObject.Parse(message);
        var name = json.Value<string>("action");
        if (!IsRemoteActionAllowed(name))
        {
            Logger.Warn("Remote command \"{0}\" is not allowed", name);
            return;
        }

        if (_shortcutManager == null || !_shortcutManager.TryGetAction(name, out var action) || action is not AbstractShortcutAction concreteAction)
        {
            Logger.Warn("Remote command \"{0}\" is not a known action", name);
            return;
        }

        var args = json["args"] as JArray ?? [];
        var argumentTypes = action.GetType().GetGenericArguments();
        if (args.Count != argumentTypes.Length || argumentTypes.Any(t => t.IsAssignableTo(typeof(MultiFunPlayer.Input.IInputGestureData))))
        {
            Logger.Warn("Remote command \"{0}\" has the wrong arguments", name);
            return;
        }

        var arguments = args.Select((a, i) => a.ToObject(argumentTypes[i])).ToArray();
        Logger.Info("Remote command \"{0}\" [Arguments: {1}]", name, string.Join(", ", arguments));
        await concreteAction.Invoke(arguments);
    }

    // Built-in mode sequence of the HISMITH S2: runs the machine's preset modes 1-8 in turn, each for
    // HismithSequenceSeconds, with the funscript output to that machine paused. Hold keeps the current
    // mode (and freezes its countdown) until released; Exit goes back to the funscripts.
    private CancellationTokenSource _hismithSequenceSource;
    private bool _hismithSequenceHeld;
    private int _hismithSequenceMode;
    private double _hismithSequenceRemaining;
    private int _hismithSequenceSeconds = 120;
    private volatile bool _forceResend;
    private volatile bool _hismithSequenceExiting;

    public bool HismithSequenceActive
    {
        get => _hismithSequenceSource != null;
        set
        {
            if (value)
                StartHismithSequence();
            else
                StopHismithSequence();
        }
    }

    public bool HismithSequenceHeld
    {
        get => _hismithSequenceHeld;
        set
        {
            if (!HismithSequenceActive && value)
                return;

            if (SetAndNotify(ref _hismithSequenceHeld, value))
                NotifyOfPropertyChange(nameof(HismithSequenceStatus));
        }
    }

    public int HismithSequenceSeconds
    {
        get => _hismithSequenceSeconds;
        set => SetAndNotify(ref _hismithSequenceSeconds, Math.Clamp(value, 5, 3600));
    }

    public string HismithSequenceStatus => !HismithSequenceActive
        ? "off"
        : $"mode {_hismithSequenceMode}/{PresetModeMax}, {TimeSpan.FromSeconds(Math.Ceiling(_hismithSequenceRemaining)):m\\:ss}{(_hismithSequenceHeld ? " (held)" : string.Empty)}";

    // Only the machine itself (the device with the "Preset mode" actuator) is affected by the mode
    // sequence; other devices, such as the Lube Launcher, are left alone.
    private bool IsHismithMachine(ButtplugDeviceSettings settings)
    {
        var device = GetDeviceFromSettings(settings);
        return device != null && device.ScalarActuators.Any(a => a.ActuatorType == ActuatorType.Position
                                                              && a.FeatureDescriptor?.StartsWith(PresetModeDescription, StringComparison.OrdinalIgnoreCase) == true);
    }

    private bool IsSequenceSuppressed(ButtplugDeviceSettings settings)
        => (HismithSequenceActive || _hismithSequenceExiting) && IsHismithMachine(settings);

    public void StartHismithSequence()
    {
        if (HismithSequenceActive)
            return;

        if (!GetQuickActuators(PresetModeDescription).Any())
        {
            Logger.Info("No device with a \"{0}\" actuator is connected, not starting the mode sequence", PresetModeDescription);
            return;
        }

        EventAggregator.Publish(new ScriptLoopMessage(ScriptLoopCommand.Clear));

        _hismithSequenceHeld = false;
        _hismithSequenceMode = 1;
        _hismithSequenceRemaining = _hismithSequenceSeconds;
        _hismithSequenceSource = new CancellationTokenSource();
        _ = RunHismithSequenceAsync(_hismithSequenceSource.Token);

        NotifyOfPropertyChange(nameof(HismithSequenceActive));
        NotifyOfPropertyChange(nameof(HismithSequenceHeld));
        NotifyOfPropertyChange(nameof(HismithSequenceStatus));
    }

    public void StopHismithSequence(bool sendToDevice = true)
    {
        var source = _hismithSequenceSource;
        if (source == null)
            return;

        _hismithSequenceSource = null;
        _hismithSequenceHeld = false;
        source.Cancel();
        source.Dispose();

        // back to the funscripts: manual mode first, then the current script values again. The script
        // output for the machine stays paused until the manual mode command has been sent, so it cannot
        // overtake it (or be overtaken by the last preset mode command still in flight).
        _hismithManualMode = true;
        _hismithPresetMode = 0;
        if (sendToDevice)
        {
            _hismithSequenceExiting = true;
            _ = ExitHismithSequenceAsync();
        }
        else
        {
            _forceResend = true;
        }

        NotifyOfPropertyChange(nameof(HismithSequenceActive));
        NotifyOfPropertyChange(nameof(HismithSequenceHeld));
        NotifyOfPropertyChange(nameof(HismithSequenceStatus));
        NotifyOfPropertyChange(nameof(HismithManualMode));
        NotifyOfPropertyChange(nameof(HismithPresetMode));
    }

    private async Task ExitHismithSequenceAsync()
    {
        try
        {
            await Task.Delay(300);
            await SendQuickActuatorsAsync(RunModeDescription, 1, 1);
        }
        finally
        {
            _forceResend = true;
            _hismithSequenceExiting = false;
        }
    }

    // One key, state aware: single press = hold/release the sequence, or the script loop when no sequence runs.
    public void HismithKeySinglePress()
    {
        if (HismithSequenceActive)
            HismithSequenceHeld = !HismithSequenceHeld;
        else
            EventAggregator.Publish(new ScriptLoopMessage(ScriptLoopCommand.Toggle));
    }

    // Double press = start the sequence, or go back to the funscripts when it is running (held or not).
    public void HismithKeyDoublePress()
        => HismithSequenceActive = !HismithSequenceActive;

    private async Task RunHismithSequenceAsync(CancellationToken token)
    {
        try
        {
            // auto mode first, the preset modes only run there
            _hismithManualMode = false;
            NotifyOfPropertyChange(nameof(HismithManualMode));
            await SendQuickActuatorsAsync(RunModeDescription, 0, 1);
            await Task.Delay(300, token);

            while (!token.IsCancellationRequested)
            {
                _hismithPresetMode = _hismithSequenceMode;
                NotifyOfPropertyChange(nameof(HismithPresetMode));
                _hismithSequenceRemaining = _hismithSequenceSeconds;
                await SendQuickActuatorsAsync(PresetModeDescription, _hismithSequenceMode, PresetModeMax);

                var lastStatusSecond = -1;
                while (_hismithSequenceRemaining > 0)
                {
                    await Task.Delay(100, token);
                    if (!_hismithSequenceHeld)
                        _hismithSequenceRemaining -= 0.1;

                    var second = (int)Math.Ceiling(_hismithSequenceRemaining);
                    if (second != lastStatusSecond)
                    {
                        lastStatusSecond = second;
                        NotifyOfPropertyChange(nameof(HismithSequenceStatus));
                    }
                }

                _hismithSequenceMode = _hismithSequenceMode % PresetModeMax + 1;
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception e)
        {
            Logger.Debug(e, "Hismith mode sequence exception");
        }
    }

    private IEnumerable<ButtplugDeviceScalarActuator> GetQuickActuators(string descriptionPrefix, ActuatorType actuatorType = ActuatorType.Position)
        => AvailableDevices.ToList()
                           .SelectMany(d => d.ScalarActuators)
                           .Where(a => a.ActuatorType == actuatorType
                                    && a.FeatureDescriptor?.StartsWith(descriptionPrefix, StringComparison.OrdinalIgnoreCase) == true);

    private async Task SendQuickActuatorsAsync(string descriptionPrefix, int value, int max, ActuatorType actuatorType = ActuatorType.Position)
    {
        try
        {
            foreach (var actuator in GetQuickActuators(descriptionPrefix, actuatorType).ToList())
            {
                Logger.Debug("Sending \"{value}/{max}\" to \"{actuator}\"", value, max, actuator);
                await actuator.ScalarAsync(Math.Clamp(value / (double)max, 0, 1), CancellationToken.None);
            }
        }
        catch (Exception e)
        {
            Logger.Debug(e, "Buttplug quick control exception");
        }
    }

    public ObservableConcurrentCollection<ButtplugDeviceSettings> DeviceSettings { get; }

    public ButtplugOutputTarget(int instanceIndex, IEventAggregator eventAggregator, IDeviceAxisValueProvider valueProvider)
        : base(instanceIndex, eventAggregator, valueProvider)
    {
        AvailableDevices = [];
        DeviceSettings = [];

        AvailableDevices.CollectionChanged += (s, e) =>
        {
            DeviceSettings.Refresh();
            NotifyOfPropertyChange(nameof(HasHismithControls));
            NotifyOfPropertyChange(nameof(HasLubeLauncherControls));
        };

        _scanSemaphore = new SemaphoreSlim(0, 1);
    }

    protected override IUpdateContext RegisterUpdateContext(DeviceAxisUpdateType updateType) => updateType switch
    {
        DeviceAxisUpdateType.FixedUpdate => new AsyncFixedUpdateContext() { UpdateInterval = 50, MinimumUpdateInterval = 16, MaximumUpdateInterval = 200 },
        DeviceAxisUpdateType.PolledUpdate => new AsyncPolledUpdateContext(),
        _ => throw new NotImplementedException(),
    };

    public bool IsScanBusy { get; set; }
    public bool CanScan => IsConnected;
    public void ToggleScan() => _scanSemaphore.Release();

    protected override ValueTask<bool> OnConnectingAsync(ConnectionType connectionType)
    {
        if (connectionType != ConnectionType.AutoConnect)
            Logger.Info("Connecting to {0} at \"{1}\" [Type: {2}]", Identifier, Endpoint?.ToUriString(), connectionType);

        if (Endpoint == null)
            throw new OutputTargetException("Endpoint cannot be null");

        return ValueTask.FromResult(true);
    }

    protected override async Task RunAsync(ConnectionType connectionType, CancellationToken token)
    {
        void OnDeviceRemoved(ButtplugDevice device)
        {
            Logger.Info($"Device removed: \"{device.Name}\"");

            AvailableDevices.Remove(device);
            if (device == SelectedDevice)
                SelectedDevice = null;
        }

        void OnDeviceAdded(ButtplugDevice device)
        {
            Logger.Info($"Device added: \"{device.Name}\"");

            AvailableDevices.Add(device);

            var syncAxes = GetSettingsForDevice(device)
                                .GroupBy(s => s.SourceAxis)
                                .Select(g => g.Key);
            EventAggregator.Publish(new SyncRequestMessage(syncAxes));
        }

        var converter = new ButtplugNewtonsoftJsonConverter();
        await using var client = new ButtplugClient(nameof(MultiFunPlayer), converter);

        client.DeviceAdded += (_, device) => OnDeviceAdded(device);
        client.DeviceRemoved += (_, device) => OnDeviceRemoved(device);
        client.UnhandledException += (_, exception) => Logger.Debug(exception);

        try
        {
            using var cancellationSource = CancellationTokenSource.CreateLinkedTokenSource(token);
            if (connectionType == ConnectionType.AutoConnect)
                cancellationSource.CancelAfter(500);

            await client.ConnectAsync(new Uri($"ws://{Endpoint.ToUriString()}"), cancellationSource.Token);
            Status = ConnectionStatus.Connected;
        }
        catch (Exception e) when (connectionType != ConnectionType.AutoConnect)
        {
            Logger.Error(e, "Error when connecting to {0} at \"{1}\"", Name, Endpoint?.ToUriString());
            _ = DialogHelper.ShowErrorAsync(e, $"Error when connecting to {Name}", "RootDialog");
            return;
        }
        catch
        {
            return;
        }

        try
        {
            EventAggregator.Publish(new SyncRequestMessage());
            if (_scanSemaphore.CurrentCount == 0)
                _scanSemaphore.Release();

            using var cancellationSource = CancellationTokenSource.CreateLinkedTokenSource(token);
            var task = await Task.WhenAny(FixedUpdateAsync(client, cancellationSource.Token), PolledUpdateAsync(client, cancellationSource.Token), ScanAsync(client, cancellationSource.Token));
            cancellationSource.Cancel();

            task.ThrowIfFaulted();
        }
        catch (OperationCanceledException) { }
        catch (Exception e)
        {
            Logger.Error(e, $"{Identifier} failed with exception");
            _ = DialogHelper.ShowErrorAsync(e, $"{Identifier} failed with exception", "RootDialog");
        }

        StopHismithSequence(sendToDevice: false);
        AvailableDevices.Clear();
    }

    private async Task FixedUpdateAsync(ButtplugClient client, CancellationToken token)
    {
        var currentValues = DeviceAxis.All.ToDictionary(a => a, _ => double.NaN);
        var lastSentValuesPerDevice = new Dictionary<ButtplugDevice, Dictionary<DeviceAxis, double>>();
        bool CheckDirtyAndUpdate(ButtplugDeviceSettings settings)
        {
            if (settings.UpdateType != DeviceAxisUpdateType.FixedUpdate)
                return false;

            if (IsSequenceSuppressed(settings))
                return false;

            var device = GetDeviceFromSettings(settings);
            if (device == null)
                return false;

            if (!lastSentValuesPerDevice.ContainsKey(device))
                lastSentValuesPerDevice.Add(device, DeviceAxis.All.ToDictionary(a => a, _ => double.NaN));

            var axis = settings.SourceAxis;
            var lastSentValues = lastSentValuesPerDevice[device];
            var currentValue = currentValues[axis];
            var lastValue = lastSentValues[axis];

            if (!double.IsFinite(currentValue))
                return false;
            if (!AxisSettings[axis].Enabled)
                return false;

            var shouldUpdate = (_forceResend && IsHismithMachine(settings))
                            || !double.IsFinite(lastValue)
                            || (currentValue == 0 && lastValue != 0)
                            || Math.Abs(lastValue - currentValue) >= 0.005;

            if (shouldUpdate)
                lastSentValues[axis] = currentValue;

            return shouldUpdate;
        }

        await FixedUpdateAsync(() => !token.IsCancellationRequested && client.IsConnected, async (_, elapsed) =>
        {
            Logger.Trace("Begin FixedUpdate [Elapsed: {0}]", elapsed);
            GetValues(currentValues, x => x < 0.005 ? 0 : x);

            foreach (var orphanedDevice in lastSentValuesPerDevice.Keys.Except(AvailableDevices))
                lastSentValuesPerDevice.Remove(orphanedDevice);

            foreach (var orphanedDevice in AvailableDevices.Where(d => lastSentValuesPerDevice.ContainsKey(d) && !GetSettingsForDevice(d).Any()))
            {
                await orphanedDevice.StopAsync(token);
                lastSentValuesPerDevice.Remove(orphanedDevice);
            }

            var dirtySettings = DeviceSettings.Where(CheckDirtyAndUpdate).ToList();
            _forceResend = false;
            var tasks = GetDeviceTasks(currentValues, elapsed * 1000, dirtySettings, token).ToList();

            try
            {
                await Task.WhenAll(tasks);
            }
            catch (Exception)
            {
                foreach (var exception in tasks.Where(t => t.Exception != null).Select(t => t.Exception))
                    Logger.Debug(exception, "Buttplug device exception");
            }
        }, token);
    }

    private async Task PolledUpdateAsync(ButtplugClient client, CancellationToken token)
    {
        await PolledUpdateAsync(DeviceAxis.All, () => !token.IsCancellationRequested && client.IsConnected, async (_, axis, axisEvent, elapsed) =>
        {
            Logger.Trace("Begin PolledUpdate [Axis: {0}, Event: {1}, Elapsed: {2}]", axis, axisEvent, elapsed);

            var settings = DeviceSettings.Where(x => x.SourceAxis == axis && x.UpdateType == DeviceAxisUpdateType.PolledUpdate && !IsSequenceSuppressed(x));
            var tasks = GetDeviceTasks(axisEvent, settings, token).ToList();

            try
            {
                await Task.WhenAll(tasks);
            }
            catch (Exception)
            {
                foreach (var exception in tasks.Where(t => t.Exception != null).Select(t => t.Exception))
                    Logger.Debug(exception, "Buttplug device exception");
            }
        }, token);
    }

    private ButtplugDevice GetDeviceFromSettings(ButtplugDeviceSettings settings)
        => GetDeviceByNameAndIndex(settings.DeviceName, settings.DeviceIndex);
    private ButtplugDevice GetDeviceByNameAndIndex(string deviceName, uint deviceIndex)
        => AvailableDevices.FirstOrDefault(d => string.Equals(deviceName, d.Name, StringComparison.OrdinalIgnoreCase) && deviceIndex == d.Index);
    private IEnumerable<ButtplugDeviceSettings> GetSettingsForDevice(ButtplugDevice device)
        => DeviceSettings.Where(s => string.Equals(s.DeviceName, device.Name, StringComparison.OrdinalIgnoreCase) && s.DeviceIndex == device.Index);

    private IEnumerable<Task> GetDeviceTasks(Dictionary<DeviceAxis, double> values, double interval, IEnumerable<ButtplugDeviceSettings> settings, CancellationToken token)
        => GetDeviceTasks(settings, (s, a) =>
        {
            var value = values[s.SourceAxis];
            if (a is ButtplugDeviceLinearActuator linearActuator)
            {
                var duration = (uint)Math.Floor(interval + 0.75);
                Logger.Trace("Sending \"{value} (Duration={duration})\" to \"{actuator}\"", value, duration, a);
                return linearActuator.LinearAsync(duration, value, token);
            }
            else if (a is ButtplugDeviceRotateActuator rotateActuator)
            {
                var speed = Math.Clamp(Math.Abs(value - 0.5) / 0.5, 0, 1);
                var clockwise = value > 0.5;
                Logger.Trace("Sending \"{speed} (Clockwise={clockwise})\" to \"{actuator}\"", speed, clockwise, a);
                return rotateActuator.RotateAsync(speed, clockwise, token);
            }
            else if (a is ButtplugDeviceScalarActuator scalarActuator)
            {
                Logger.Trace("Sending \"{value}\" to \"{actuator}\"", value, a);
                return scalarActuator.ScalarAsync(value, token);
            }

            return Task.CompletedTask;
        }, token);

    private IEnumerable<Task> GetDeviceTasks(DeviceAxisValueEvent axisEvent, IEnumerable<ButtplugDeviceSettings> settings, CancellationToken token)
        => GetDeviceTasks(settings, (s, a) =>
        {
            if (!double.IsFinite(axisEvent.TargetValue))
                return Task.CompletedTask;

            var axisSettings = AxisSettings[s.SourceAxis];
            var value = MathUtils.Lerp(axisSettings.Minimum, axisSettings.Maximum, axisEvent.TargetValue);
            if (a is ButtplugDeviceLinearActuator linearActuator)
            {
                var duration = (uint)Math.Floor(axisEvent.Duration * 1000 + 0.75);
                Logger.Trace("Sending \"{value} (Duration={duration})\" to \"{actuator}\"", value, duration, a);
                return linearActuator.LinearAsync(duration, value, token);
            }
            else if (a is ButtplugDeviceRotateActuator rotateActuator)
            {
                var speed = Math.Clamp(Math.Abs(value - 0.5) / 0.5, 0, 1);
                var clockwise = value > 0.5;
                Logger.Trace("Sending \"{speed} (Clockwise={clockwise})\" to \"{actuator}\"", speed, clockwise, a);
                return rotateActuator.RotateAsync(speed, clockwise, token);
            }
            else if (a is ButtplugDeviceScalarActuator scalarActuator)
            {
                Logger.Trace("Sending \"{value}\" to \"{actuator}\"", value, a);
                return scalarActuator.ScalarAsync(value, token);
            }

            return Task.CompletedTask;
        }, token);

    private IEnumerable<Task> GetDeviceTasks(IEnumerable<ButtplugDeviceSettings> settings, Func<ButtplugDeviceSettings, ButtplugDeviceActuator, Task> taskFactory, CancellationToken token)
        => settings.GroupBy(m => m.DeviceName).SelectMany(deviceGroup =>
        {
            var deviceName = deviceGroup.Key;
            return deviceGroup.GroupBy(m => m.DeviceIndex).SelectMany(indexGroup =>
            {
                var deviceIndex = indexGroup.Key;
                var device = GetDeviceByNameAndIndex(deviceName, deviceIndex);
                if (device == null)
                    return [];

                return indexGroup.GroupBy(m => m.ActuatorType).SelectMany(typeGroup => typeGroup.Select(s =>
                {
                    if (!device.TryGetActuator(s.ActuatorIndex, s.ActuatorType, out var actuator))
                        return Task.CompletedTask;

                    return taskFactory(s, actuator);
                }));
            });
        });

    private async Task ScanAsync(ButtplugClient client, CancellationToken token)
    {
        var scanTask = default(Task);
        var cancellationSource = default(CancellationTokenSource);
        while (!token.IsCancellationRequested)
        {
            await _scanSemaphore.WaitAsync(token);
            if (scanTask?.IsCompleted == false)
            {
                try { cancellationSource?.Cancel(); }
                catch { }
            }
            else
            {
                scanTask = DoScanAsync();
            }
        }

        void OnScanningFinished(object sender, EventArgs e) => cancellationSource.Cancel();
        async Task DoScanAsync()
        {
            try
            {
                cancellationSource = new CancellationTokenSource();
                cancellationSource.CancelAfter(TimeSpan.FromSeconds(30));

                IsScanBusy = true;

                Logger.Debug("Starting device scan");

                client.ScanningFinished += OnScanningFinished;
                await client.StartScanningAsync(token);

                try { await cancellationSource.Token.WaitHandle.WaitOneAsync(token); }
                catch { }

                Logger.Debug("Stopping device scan");
                await client.StopScanningAsync(token);
            }
            finally
            {
                client.ScanningFinished -= OnScanningFinished;
                IsScanBusy = false;

                cancellationSource?.Dispose();
                cancellationSource = null;
            }
        }
    }

    public override void HandleSettings(JObject settings, SettingsAction action)
    {
        base.HandleSettings(settings, action);

        if (action == SettingsAction.Saving)
        {
            settings[nameof(Endpoint)] = Endpoint?.ToUriString();
            settings[nameof(DeviceSettings)] = JArray.FromObject(DeviceSettings);
            settings[nameof(RemoteCommandsEnabled)] = RemoteCommandsEnabled;
            settings[nameof(RemoteCommandsPort)] = RemoteCommandsPort;
        }
        else if (action == SettingsAction.Loading)
        {
            if (settings.TryGetValue<EndPoint>(nameof(Endpoint), out var endpoint))
                Endpoint = endpoint;

            if (settings.TryGetValue<List<ButtplugDeviceSettings>>(nameof(DeviceSettings), out var deviceSettings))
                DeviceSettings.SetFrom(deviceSettings.Where(d => d.SourceAxis != null));

            if (settings.TryGetValue<int>(nameof(RemoteCommandsPort), out var remoteCommandsPort))
                RemoteCommandsPort = remoteCommandsPort;
            if (settings.TryGetValue<bool>(nameof(RemoteCommandsEnabled), out var remoteCommandsEnabled))
                RemoteCommandsEnabled = remoteCommandsEnabled;
        }
    }

    public override void RegisterActions(IShortcutManager s)
    {
        base.RegisterActions(s);
        _shortcutManager = s;

        #region Endpoint
        s.RegisterAction<string>($"{Identifier}::Endpoint::Set", s => s.WithLabel("Endpoint").WithDescription("ipOrHost:port"), endpointString =>
        {
            if (NetUtils.TryParseEndpoint(endpointString, out var endpoint))
                Endpoint = endpoint;
        });
        #endregion

        #region Hismith quick controls
        s.RegisterAction<bool>($"{Identifier}::Hismith::ManualMode::Set", s => s.WithLabel("Manual run mode"), manual => HismithManualMode = manual);
        s.RegisterAction<int>($"{Identifier}::Hismith::PresetMode::Set", s => s.WithLabel("Preset mode (0-8)"), mode => HismithPresetMode = mode);
        s.RegisterAction($"{Identifier}::Hismith::Key::SinglePress", HismithKeySinglePress);
        s.RegisterAction($"{Identifier}::Hismith::Key::DoublePress", HismithKeyDoublePress);
        s.RegisterAction($"{Identifier}::Hismith::Sequence::Start", StartHismithSequence);
        s.RegisterAction($"{Identifier}::Hismith::Sequence::Stop", () => StopHismithSequence());
        s.RegisterAction($"{Identifier}::Hismith::Sequence::Hold::Toggle", () => HismithSequenceHeld = !HismithSequenceHeld);
        s.RegisterAction<bool>($"{Identifier}::Hismith::Sequence::Hold::Set", s => s.WithLabel("Hold the current mode"), held => HismithSequenceHeld = held);
        s.RegisterAction<int>($"{Identifier}::Hismith::Speed::Set", s => s.WithLabel("Speed (0-100)"), speed => HismithSpeed = speed);
        s.RegisterAction<int>($"{Identifier}::Hismith::Speed::Offset", s => s.WithLabel("Speed offset"), offset => HismithSpeed += offset);
        s.RegisterAction($"{Identifier}::Hismith::LubeLauncher::Fire", FireLubeLauncher);
        s.RegisterAction<bool>($"{Identifier}::Hismith::LubeLauncher::AutoSpray::Set", s => s.WithLabel("Lube Launcher auto spray"), enabled => LubeAutoSpray = enabled);
        #endregion
    }

    public override void UnregisterActions(IShortcutManager s)
    {
        base.UnregisterActions(s);
        s.UnregisterAction($"{Identifier}::Endpoint::Set");
        s.UnregisterAction($"{Identifier}::Hismith::ManualMode::Set");
        s.UnregisterAction($"{Identifier}::Hismith::PresetMode::Set");
        s.UnregisterAction($"{Identifier}::Hismith::Key::SinglePress");
        s.UnregisterAction($"{Identifier}::Hismith::Key::DoublePress");
        s.UnregisterAction($"{Identifier}::Hismith::Sequence::Start");
        s.UnregisterAction($"{Identifier}::Hismith::Sequence::Stop");
        s.UnregisterAction($"{Identifier}::Hismith::Sequence::Hold::Toggle");
        s.UnregisterAction($"{Identifier}::Hismith::Sequence::Hold::Set");
        s.UnregisterAction($"{Identifier}::Hismith::Speed::Set");
        s.UnregisterAction($"{Identifier}::Hismith::Speed::Offset");
        s.UnregisterAction($"{Identifier}::Hismith::LubeLauncher::Fire");
        s.UnregisterAction($"{Identifier}::Hismith::LubeLauncher::AutoSpray::Set");
    }

    public override void RegisterProperties(IPropertyManager p)
    {
        base.RegisterProperties(p);
        p.RegisterProperty($"{Identifier}::Endpoint", () => Endpoint);
    }

    public override void UnregisterProperties(IPropertyManager p)
    {
        base.UnregisterProperties(p);
        p.UnregisterProperty($"{Identifier}::Endpoint");

    }

    public void OnSettingsAdd()
    {
        SelectedDevice.TryGetActuator(SelectedActuatorIndex.Value, SelectedActuatorType.Value, out var selectedActuator);

        DeviceSettings.Add(new()
        {
            DeviceName = SelectedDevice.Name,
            DeviceIndex = SelectedDevice.Index,
            SourceAxis = SelectedDeviceAxis,
            ActuatorIndex = SelectedActuatorIndex.Value,
            ActuatorType = SelectedActuatorType.Value,
            ActuatorDescription = selectedActuator?.FeatureDescriptor,
            UpdateType = DeviceAxisUpdateType.FixedUpdate
        });

        SelectedDevice = null;
        SelectedDeviceAxis = null;
        SelectedActuatorIndex = null;
        SelectedActuatorType = null;
    }

    public void OnSettingsDelete(object sender, EventArgs e)
    {
        if (sender is not FrameworkElement element || element.DataContext is not ButtplugDeviceSettings settings)
            return;

        DeviceSettings.Remove(settings);
        NotifyOfPropertyChange(nameof(AvailableActuatorIndices));
    }

    protected override void Dispose(bool disposing)
    {
        base.Dispose(disposing);

        _lubeAutoCancellationSource?.Cancel();
        _lubeAutoCancellationSource?.Dispose();
        _lubeAutoCancellationSource = null;

        _scanSemaphore?.Dispose();
        _scanSemaphore = null;
    }
}

[JsonObject(MemberSerialization.OptIn)]
internal sealed class ButtplugDeviceSettings : PropertyChangedBase
{
    [JsonProperty] public string DeviceName { get; set; }
    [JsonProperty] public uint DeviceIndex { get; set; }
    [JsonProperty] public DeviceAxis SourceAxis { get; set; }
    [JsonProperty] public ActuatorType ActuatorType { get; set; }
    [JsonProperty] public uint ActuatorIndex { get; set; }
    [JsonProperty] public string ActuatorDescription { get; set; }
    [JsonProperty] public DeviceAxisUpdateType UpdateType { get; set; }

    public string ActuatorLabel => string.IsNullOrWhiteSpace(ActuatorDescription) ? null : $" ({ActuatorDescription})";
}

internal sealed record ButtplugActuatorChoice(uint Index, string Description)
{
    public override string ToString() => string.IsNullOrWhiteSpace(Description) ? $"{Index}" : $"{Index} - {Description}";
}
