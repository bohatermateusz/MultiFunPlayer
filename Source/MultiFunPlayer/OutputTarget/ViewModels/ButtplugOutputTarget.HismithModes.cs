using Buttplug;
using MultiFunPlayer.Common;
using MultiFunPlayer.Shortcut;
using Newtonsoft.Json.Linq;
using Stylet;

namespace MultiFunPlayer.OutputTarget.ViewModels;

// HISMITH S2: "boost" of the funscript (faster/slower/deeper/shallower on top of the script values),
// the built-in modes 1-8 plus the two custom modes A (fastest, full depth, smallest stroke) and
// B (the same, slowest), and the way back to the funscripts. The actuators are found by their
// description (see the Buttplug fork), so only a machine that has them is affected.
internal sealed partial class ButtplugOutputTarget
{
    private const string DepthDescription = "Depth (";
    private const string WidthDescription = "Stroke width";
    private const string DirectPositionDescription = "Position (move to depth now)";
    private const int ModeA = 9;
    private const int ModeB = 10;
    private const int ModeCalm = 12;               // slowest speed, medium depth: "enough" without stopping
    private const int ModeHalt = 11;               // speed 0, the script is held back for the machine until another mode
    private const int SmallestStrokeWidth = 10;
    private const double SurpriseModeChance = 0.7;   // chance that the surprise also changes the machine's mode

    private volatile int _hismithMode;           // 0 = funscripts, 1-8 = built-in mode, 9 = A, 10 = B
    private int _hismithSpeedBoost;
    private int _hismithDepthBoost;
    private int _hismithSpeedStep = 20;
    private int _hismithDepthStep = 15;
    private int _hismithDepth = 50;              // absolute depth (0-100) used while no script drives the machine
    private int _hismithSlowestSpeed = 1;
    private int _hismithScriptWidth = 40;        // stroke width the scripts run with (the machine's default window)
    private readonly SemaphoreSlim _modeGate = new(1, 1);

    public int HismithMode => _hismithMode;
    public bool HismithModeIsScript => _hismithMode == 0;

    public int HismithSpeedStep
    {
        get => _hismithSpeedStep;
        set => SetAndNotify(ref _hismithSpeedStep, Math.Clamp(value, 1, 100));
    }

    public int HismithDepthStep
    {
        get => _hismithDepthStep;
        set => SetAndNotify(ref _hismithDepthStep, Math.Clamp(value, 1, 100));
    }

    // Boost: added to the script's value (-100..100 percentage points). Changing it re-sends the machine's values.
    public int HismithSpeedBoost
    {
        get => _hismithSpeedBoost;
        set
        {
            if (SetAndNotify(ref _hismithSpeedBoost, Math.Clamp(value, -100, 100)))
            {
                _forceResend = true;
                NotifyOfPropertyChange(nameof(HismithModeStatus));
            }
        }
    }

    public int HismithDepthBoost
    {
        get => _hismithDepthBoost;
        set
        {
            if (SetAndNotify(ref _hismithDepthBoost, Math.Clamp(value, -100, 100)))
            {
                _forceResend = true;
                NotifyOfPropertyChange(nameof(HismithModeStatus));
            }
        }
    }

    // Absolute depth, used in the built-in modes and when no axis is mapped to the depth actuator.
    public int HismithDepth
    {
        get => _hismithDepth;
        set
        {
            if (SetAndNotify(ref _hismithDepth, Math.Clamp(value, 0, 100)))
            {
                _ = SendQuickActuatorsAsync(DepthDescription, _hismithDepth, 100);
                NotifyOfPropertyChange(nameof(HismithModeStatus));
            }
        }
    }

    public string HismithModeStatus
    {
        get
        {
            var mode = _hismithMode switch
            {
                0 => "funscript",
                ModeA => "A (fastest, full depth, smallest stroke)",
                ModeB => "B (slowest, full depth, smallest stroke)",
                ModeHalt => "STOPPED (speed 0)",
                ModeCalm => "CALM (slowest, medium depth)",
                _ => $"built-in mode {_hismithMode}"
            };

            var text = _hismithMode == 0
                ? $"{mode}; boost: speed {_hismithSpeedBoost:+0;-0;0}, depth {_hismithDepthBoost:+0;-0;0}"
                : $"{mode}; speed {HismithSpeed}, depth {_hismithDepth}";
            return _surpriseRemaining > 0 ? $"{text}; next surprise in {TimeSpan.FromSeconds(_surpriseRemaining):m\\:ss}" : text;
        }
    }

    private void NotifyModeChanged()
    {
        NotifyOfPropertyChange(nameof(HismithMode));
        NotifyOfPropertyChange(nameof(HismithModeIsScript));
        NotifyOfPropertyChange(nameof(HismithModeStatus));
    }

    private bool HasDirectPositionActuator => GetQuickActuators(DirectPositionDescription).Any();

    // true when an axis is mapped to the actuator whose description starts with the prefix
    private bool IsActuatorMapped(string descriptionPrefix, ActuatorType actuatorType)
        => DeviceSettings.ToList().Any(s => s.ActuatorType == actuatorType
                                         && GetDeviceFromSettings(s) is { } device
                                         && device.TryGetActuator(s.ActuatorIndex, s.ActuatorType, out var actuator)
                                         && actuator is ButtplugDeviceScalarActuator scalar
                                         && scalar.FeatureDescriptor?.StartsWith(descriptionPrefix, StringComparison.OrdinalIgnoreCase) == true);

    // The funscript value for a speed or depth actuator plus the boost; any other actuator is left alone.
    private double ApplyBoost(ButtplugDeviceActuator actuator, double value)
    {
        if (actuator is not ButtplugDeviceScalarActuator scalar || scalar.FeatureDescriptor == null)
            return value;

        if (_hismithSpeedBoost != 0 && scalar.ActuatorType == ActuatorType.Oscillate
         && scalar.FeatureDescriptor.StartsWith(SpeedDescription, StringComparison.OrdinalIgnoreCase))
            return Math.Clamp(value + _hismithSpeedBoost / 100d, 0, 1);

        if (_hismithDepthBoost != 0 && scalar.ActuatorType == ActuatorType.Position
         && scalar.FeatureDescriptor.StartsWith(DepthDescription, StringComparison.OrdinalIgnoreCase))
            return Math.Clamp(value + _hismithDepthBoost / 100d, 0, 1);

        return value;
    }

    // "faster" etc.: on top of the script when an axis drives that actuator and no built-in mode runs,
    // otherwise an absolute value (the machine has no script to boost).
    public void HismithFaster() => ChangeSpeed(HismithSpeedStep);
    public void HismithSlower() => ChangeSpeed(-HismithSpeedStep);
    public void HismithDeeper() => ChangeDepth(HismithDepthStep);
    public void HismithShallower() => ChangeDepth(-HismithDepthStep);

    private void ChangeSpeed(int delta)
    {
        if (_hismithMode == 0 && IsActuatorMapped(SpeedDescription, ActuatorType.Oscillate))
            HismithSpeedBoost += delta;
        else
            HismithSpeed += delta;
        NotifyOfPropertyChange(nameof(HismithModeStatus));
    }

    private void ChangeDepth(int delta)
    {
        if (_hismithMode == 0 && IsActuatorMapped(DepthDescription, ActuatorType.Position))
            HismithDepthBoost += delta;
        else
            HismithDepth += delta;
        NotifyOfPropertyChange(nameof(HismithModeStatus));
    }

    public void HismithBoostReset()
    {
        HismithSpeedBoost = 0;
        HismithDepthBoost = 0;
    }

    public void HismithMany(int count) => _ = FireLubeLauncherManyAsync(Math.Clamp(count, 1, 5));

    // A burst of shots can be cut short (by a re-roll of the surprise or by Stop): the shots that are still to come
    // are dropped, the one that has been fired cannot be taken back.
    private CancellationTokenSource _lubeBurstSource;

    private void CancelLubeBurst()
        => Interlocked.Exchange(ref _lubeBurstSource, null)?.Cancel();

    private async Task FireLubeLauncherManyAsync(int count)
    {
        var source = new CancellationTokenSource();
        Interlocked.Exchange(ref _lubeBurstSource, source)?.Cancel();
        try
        {
            for (var i = 0; i < count && !source.IsCancellationRequested; i++)
            {
                await FireLubeLauncherAsync();
                if (i < count - 1)
                    await Task.Delay(1000, source.Token);
            }
        }
        catch (OperationCanceledException) { }
        finally
        {
            Interlocked.CompareExchange(ref _lubeBurstSource, null, source);
        }
    }

    // Button handler for the mode buttons in the view (the parameter is the mode number as text).
    public void OnHismithModeButton(string mode)
    {
        if (int.TryParse(mode, out var number))
            SetHismithMode(number);
    }

    public void HismithModeScript() => SetHismithMode(0);

    // Surprise: two independent draws. Machine: with SurpriseModeChance one of the built-in modes 1-8 or the
    // custom A / B (never the one that runs now), otherwise unchanged. Lube Launcher: nothing, one shot or three
    // shots, each with the same chance. At least one of them always happens (the surprise may be only a shot,
    // or only a mode).
    public void HismithSurprise() => _ = HismithSurpriseAsync();

    // "No, something else": the NEXT draw is made and put into effect only after HismithSurpriseDelaySeconds
    // (default 120 s), counted from the command; until then the current surprise stays. Shots of the previous
    // burst that have not been fired yet are dropped at once. Saying it again restarts the countdown. It only
    // works while a surprise is in force: choosing a mode, Script, Calm or Stop ends the surprise and cancels
    // a pending draw.
    private bool _surpriseActive;
    private CancellationTokenSource _surpriseTimerSource;
    private volatile int _surpriseDelaySeconds = 120;
    private double _surpriseRemaining;

    public int HismithSurpriseDelaySeconds
    {
        get => _surpriseDelaySeconds;
        set => SetAndNotify(ref _surpriseDelaySeconds, Math.Clamp(value, 5, 3600));
    }

    private void EndSurprise()
    {
        _surpriseActive = false;
        Interlocked.Exchange(ref _surpriseTimerSource, null)?.Cancel();
        _surpriseRemaining = 0;
        NotifyOfPropertyChange(nameof(HismithModeStatus));
    }

    public void HismithSurpriseAgain()
    {
        if (!_surpriseActive)
        {
            Logger.Info("No surprise in force, nothing to draw again");
            return;
        }

        CancelLubeBurst();
        var source = new CancellationTokenSource();
        Interlocked.Exchange(ref _surpriseTimerSource, source)?.Cancel();
        _ = RunSurpriseTimerAsync(source);
    }

    private async Task RunSurpriseTimerAsync(CancellationTokenSource source)
    {
        try
        {
            _surpriseRemaining = _surpriseDelaySeconds;
            while (_surpriseRemaining > 0)
            {
                NotifyOfPropertyChange(nameof(HismithModeStatus));
                await Task.Delay(1000, source.Token);
                _surpriseRemaining -= 1;
            }

            _surpriseRemaining = 0;
            Interlocked.CompareExchange(ref _surpriseTimerSource, null, source);
            await HismithSurpriseAsync();
        }
        catch (OperationCanceledException) { }
    }

    private async Task HismithSurpriseAsync()
    {
        var hasMachine = GetQuickActuators(PresetModeDescription).Any();
        var hasLube = GetQuickActuators(SprayDescription).Any();
        if (!hasMachine && !hasLube)
            return;

        var shots = hasLube ? new[] { 0, 1, 3 }[Random.Shared.Next(3)] : 0;
        var changeMode = hasMachine && Random.Shared.NextDouble() < SurpriseModeChance;
        if (!changeMode && shots == 0)
        {
            if (hasMachine)
                changeMode = true;
            else
                shots = Random.Shared.Next(2) == 0 ? 1 : 3;
        }

        _surpriseActive = true;
        Logger.Info("Surprise: mode change {0}, Lube Launcher shots {1}", changeMode, shots);
        if (changeMode)
            HismithModeRandom();
        NotifyOfPropertyChange(nameof(HismithModeStatus));
        if (shots > 0)
            await FireLubeLauncherManyAsync(shots);
    }

    // Random mode only: one of the built-in modes 1-8 or the custom A / B, never the one that runs now.
    public void HismithModeRandom()
    {
        var current = _hismithMode;
        var choices = Enumerable.Range(1, ModeB).Where(m => m != current).ToList();
        SetHismithModeCore(choices[Random.Shared.Next(choices.Count)], fromSurprise: true);
    }

    public void SetHismithMode(int mode) => SetHismithModeCore(mode, fromSurprise: false);

    private void SetHismithModeCore(int mode, bool fromSurprise)
    {
        if (!fromSurprise)
            EndSurprise();         // a mode chosen by hand ends the surprise and any pending draw

        mode = Math.Clamp(mode, 0, ModeCalm);
        if (mode == ModeHalt && !GetQuickActuators(SpeedDescription, ActuatorType.Oscillate).Any())
        {
            Logger.Info("No device with a speed actuator is connected, nothing to stop");
            return;
        }

        if (mode is not (0 or ModeHalt) && !GetQuickActuators(PresetModeDescription).Any())
        {
            Logger.Info("No device with a \"{0}\" actuator is connected, not starting mode {1}", PresetModeDescription, mode);
            return;
        }

        _ = ApplyHismithModeAsync(mode);
    }

    private async Task SetSpeedAbsoluteAsync(int speed)
    {
        // sent directly (not through HismithSpeed) so that it also goes out when the value did not change
        _hismithSpeed = Math.Clamp(speed, 0, 100);
        NotifyOfPropertyChange(nameof(HismithSpeed));
        await SendQuickActuatorsAsync(SpeedDescription, _hismithSpeed, 100, ActuatorType.Oscillate);
    }

    private async Task ApplyHismithModeAsync(int mode)
    {
        await _modeGate.WaitAsync();
        try
        {
            // the old time-based sequence is replaced by an explicit mode, and an explicit mode by the sequence
            StopHismithSequence(sendToDevice: false);
            var previous = _hismithMode;

            if (mode == 0)
            {
                if (previous == 0)
                    return;

                await SendQuickActuatorsAsync(WidthDescription, _hismithScriptWidth, 100);
                if (previous is >= 1 and <= PresetModeMax)
                {
                    // a speed command ends a built-in mode, but the server drops a command identical to the
                    // last one: go through 0 so that the script's speed is surely sent
                    await SetSpeedAbsoluteAsync(0);
                    await Task.Delay(150);
                }

                if (HasDirectPositionActuator)
                {
                    _hismithManualMode = true;
                    NotifyOfPropertyChange(nameof(HismithManualMode));
                    await SendQuickActuatorsAsync(RunModeDescription, 1, 1);
                }

                _hismithPresetMode = 0;
                NotifyOfPropertyChange(nameof(HismithPresetMode));
                _forceResend = true;        // before the suppression is lifted, as after the sequence
                _hismithMode = 0;
                NotifyModeChanged();
                return;
            }

            if (mode == ModeHalt)
            {
                // stop: the script is held back for the machine and the speed goes to 0 at once
                _hismithMode = mode;
                NotifyModeChanged();
                CancelLubeBurst();
                EventAggregator.Publish(new ScriptLoopMessage(ScriptLoopCommand.Clear));
                await SetSpeedAbsoluteAsync(0);
                NotifyOfPropertyChange(nameof(HismithModeStatus));
                return;
            }

            _hismithMode = mode;            // the script output to the machine is suppressed from here on
            NotifyModeChanged();
            EventAggregator.Publish(new ScriptLoopMessage(ScriptLoopCommand.Clear));

            _hismithManualMode = false;
            NotifyOfPropertyChange(nameof(HismithManualMode));
            await SendQuickActuatorsAsync(RunModeDescription, 0, 1);
            await Task.Delay(150);

            if (mode <= PresetModeMax)
            {
                _hismithPresetMode = mode;
                NotifyOfPropertyChange(nameof(HismithPresetMode));
                await SendPresetModeAsync(mode);
            }
            else if (mode == ModeCalm)
            {
                // calm: the slowest speed, the normal stroke width, a medium depth
                await SendQuickActuatorsAsync(WidthDescription, _hismithScriptWidth, 100);
                _hismithDepth = 50;
                NotifyOfPropertyChange(nameof(HismithDepth));
                await SendQuickActuatorsAsync(DepthDescription, 50, 100);
                await SetSpeedAbsoluteAsync(_hismithSlowestSpeed);
            }
            else
            {
                // A and B: the smallest stroke at full depth, A at full speed, B as slow as possible
                await SendQuickActuatorsAsync(WidthDescription, SmallestStrokeWidth, 100);
                _hismithDepth = 100;
                NotifyOfPropertyChange(nameof(HismithDepth));
                await SendQuickActuatorsAsync(DepthDescription, 100, 100);
                await SetSpeedAbsoluteAsync(mode == ModeA ? 100 : _hismithSlowestSpeed);
            }

            NotifyOfPropertyChange(nameof(HismithModeStatus));
        }
        catch (Exception e)
        {
            Logger.Debug(e, "Hismith mode exception");
        }
        finally
        {
            _modeGate.Release();
        }
    }

    private void RegisterHismithModeActions(IShortcutManager s)
    {
        s.RegisterAction<int>($"{Identifier}::Hismith::Mode::Set", s => s.WithLabel("Mode (0 = funscript, 1-8 built-in, 9 = A, 10 = B, 11 = stop, 12 = calm)"), SetHismithMode);
        s.RegisterAction($"{Identifier}::Hismith::Mode::Script", HismithModeScript);
        s.RegisterAction($"{Identifier}::Hismith::Mode::Random", HismithModeRandom);
        s.RegisterAction($"{Identifier}::Hismith::Surprise", HismithSurprise);
        s.RegisterAction($"{Identifier}::Hismith::Surprise::Again", HismithSurpriseAgain);
        s.RegisterAction($"{Identifier}::Hismith::Halt", () => SetHismithMode(ModeHalt));
        s.RegisterAction($"{Identifier}::Hismith::Calm", () => SetHismithMode(ModeCalm));
        s.RegisterAction($"{Identifier}::Hismith::Speed::Faster", HismithFaster);
        s.RegisterAction($"{Identifier}::Hismith::Speed::Slower", HismithSlower);
        s.RegisterAction($"{Identifier}::Hismith::Depth::Deeper", HismithDeeper);
        s.RegisterAction($"{Identifier}::Hismith::Depth::Shallower", HismithShallower);
        s.RegisterAction($"{Identifier}::Hismith::Boost::Reset", HismithBoostReset);
        s.RegisterAction<int>($"{Identifier}::Hismith::LubeLauncher::FireMany", s => s.WithLabel("Lube Launcher: fire several times (1-5)"), HismithMany);
    }

    private void UnregisterHismithModeActions(IShortcutManager s)
    {
        s.UnregisterAction($"{Identifier}::Hismith::Mode::Set");
        s.UnregisterAction($"{Identifier}::Hismith::Mode::Script");
        s.UnregisterAction($"{Identifier}::Hismith::Mode::Random");
        s.UnregisterAction($"{Identifier}::Hismith::Surprise");
        s.UnregisterAction($"{Identifier}::Hismith::Surprise::Again");
        s.UnregisterAction($"{Identifier}::Hismith::Halt");
        s.UnregisterAction($"{Identifier}::Hismith::Calm");
        s.UnregisterAction($"{Identifier}::Hismith::Speed::Faster");
        s.UnregisterAction($"{Identifier}::Hismith::Speed::Slower");
        s.UnregisterAction($"{Identifier}::Hismith::Depth::Deeper");
        s.UnregisterAction($"{Identifier}::Hismith::Depth::Shallower");
        s.UnregisterAction($"{Identifier}::Hismith::Boost::Reset");
        s.UnregisterAction($"{Identifier}::Hismith::LubeLauncher::FireMany");
    }

    private void SaveHismithModeSettings(JObject settings)
    {
        settings[nameof(HismithSpeedStep)] = HismithSpeedStep;
        settings[nameof(HismithDepthStep)] = HismithDepthStep;
        settings[nameof(HismithSlowestSpeed)] = HismithSlowestSpeed;
        settings[nameof(HismithSurpriseDelaySeconds)] = HismithSurpriseDelaySeconds;
    }

    private void LoadHismithModeSettings(JObject settings)
    {
        if (settings.TryGetValue<int>(nameof(HismithSpeedStep), out var speedStep))
            HismithSpeedStep = speedStep;
        if (settings.TryGetValue<int>(nameof(HismithDepthStep), out var depthStep))
            HismithDepthStep = depthStep;
        if (settings.TryGetValue<int>(nameof(HismithSlowestSpeed), out var slowest))
            HismithSlowestSpeed = slowest;
        if (settings.TryGetValue<int>(nameof(HismithSurpriseDelaySeconds), out var surpriseDelay))
            HismithSurpriseDelaySeconds = surpriseDelay;
    }

    public int HismithSlowestSpeed
    {
        get => _hismithSlowestSpeed;
        set => SetAndNotify(ref _hismithSlowestSpeed, Math.Clamp(value, 1, 100));
    }

    private void ResetHismithMode()
    {
        EndSurprise();
        _hismithMode = 0;
        _hismithSpeedBoost = 0;
        _hismithDepthBoost = 0;
        NotifyModeChanged();
    }
}
