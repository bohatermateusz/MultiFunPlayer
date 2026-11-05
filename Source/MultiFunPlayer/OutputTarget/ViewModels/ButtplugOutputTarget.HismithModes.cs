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

    // mode, boosts, depth and speed live in the side state (Cur: the side of the current command, see HismithSides)
    private int _hismithSpeedStep = 20;
    private int _hismithDepthStep = 15;
    private int _hismithSlowestSpeed = 1;
    private int _hismithScriptWidth = 40;        // stroke width the scripts run with (the machine's default window)
    private readonly SemaphoreSlim _modeGate = new(1, 1);

    public int HismithMode => Cur.Mode;
    public bool HismithModeIsScript => Cur.Mode == 0;

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
        get => Cur.SpeedBoost;
        set
        {
            if (RejectedByLock("the speed boost"))
                return;
            if (SetAndNotify(ref Cur.SpeedBoost, Math.Clamp(value, -100, 100)))
            {
                _forceResend = true;
                NotifyOfPropertyChange(nameof(HismithModeStatus));
            }
        }
    }

    public int HismithDepthBoost
    {
        get => Cur.DepthBoost;
        set
        {
            if (RejectedByLock("the depth boost"))
                return;
            if (SetAndNotify(ref Cur.DepthBoost, Math.Clamp(value, -100, 100)))
            {
                _forceResend = true;
                NotifyOfPropertyChange(nameof(HismithModeStatus));
            }
        }
    }

    // Absolute depth, used in the built-in modes and when no axis is mapped to the depth actuator.
    public int HismithDepth
    {
        get => Cur.Depth;
        set
        {
            if (RejectedByLock("the depth"))
                return;
            if (SetAndNotify(ref Cur.Depth, Math.Clamp(value, 0, 100)))
            {
                _ = SendQuickActuatorsAsync(DepthDescription, Cur.Depth, 100);
                NotifyOfPropertyChange(nameof(HismithModeStatus));
            }
        }
    }

    public string HismithModeStatus
    {
        get
        {
            var sides = PresentSides();
            var text = sides.Count > 1
                ? string.Join("; ", sides.Select(sd => $"{sd}: {SideStatus(sd)}"))
                : SideStatus(HismithSide.Rear);
            if (_lockRemaining > 0)
                text += $"; HELD for {TimeSpan.FromSeconds(_lockRemaining):m\\:ss}";
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

    // The funscript value for a speed or depth actuator plus the boost of the device's side; any other actuator is
    // left alone.
    private double ApplyBoost(ButtplugDeviceSettings settings, ButtplugDeviceActuator actuator, double value)
    {
        if (actuator is not ButtplugDeviceScalarActuator scalar || scalar.FeatureDescriptor == null)
            return value;

        var state = StateOf(GetDeviceFromSettings(settings));
        if (state.SpeedBoost != 0 && scalar.ActuatorType == ActuatorType.Oscillate
         && scalar.FeatureDescriptor.StartsWith(SpeedDescription, StringComparison.OrdinalIgnoreCase))
            return Math.Clamp(value + state.SpeedBoost / 100d, 0, 1);

        if (state.DepthBoost != 0 && scalar.ActuatorType == ActuatorType.Position
         && scalar.FeatureDescriptor.StartsWith(DepthDescription, StringComparison.OrdinalIgnoreCase))
            return Math.Clamp(value + state.DepthBoost / 100d, 0, 1);

        return value;
    }

    // "faster" etc.: on top of the script when an axis drives that actuator and no built-in mode runs,
    // otherwise an absolute value (the machine has no script to boost).
    public void HismithFaster() => ForEachSide(() => ChangeSpeed(HismithSpeedStep));
    public void HismithSlower() => ForEachSide(() => ChangeSpeed(-HismithSpeedStep));
    public void HismithDeeper() => ForEachSide(() => ChangeDepth(HismithDepthStep));
    public void HismithShallower() => ForEachSide(() => ChangeDepth(-HismithDepthStep));

    private void ChangeSpeed(int delta)
    {
        if (Cur.Mode == 0 && IsActuatorMapped(SpeedDescription, ActuatorType.Oscillate))
            HismithSpeedBoost += delta;
        else
            HismithSpeed += delta;
        NotifyOfPropertyChange(nameof(HismithModeStatus));
    }

    private void ChangeDepth(int delta)
    {
        if (Cur.Mode == 0 && IsActuatorMapped(DepthDescription, ActuatorType.Position))
            HismithDepthBoost += delta;
        else
            HismithDepth += delta;
        NotifyOfPropertyChange(nameof(HismithModeStatus));
    }

    public void HismithBoostReset() => ForEachSide(BoostResetCore);

    private void BoostResetCore()
    {
        HismithSpeedBoost = 0;
        HismithDepthBoost = 0;
    }

    public void HismithMany(int count) => ForEachSide(() => _ = FireLubeLauncherManyAsync(Math.Clamp(count, 1, 5)));

    // A burst of shots can be cut short (by a re-roll of the surprise or by Stop): the shots that are still to come
    // are dropped, the one that has been fired cannot be taken back.
    // (one burst per side: the front and the rear launcher can fire at the same time)
    private void CancelLubeBurst()
    {
        foreach (var state in _sideStates.Values)
            Interlocked.Exchange(ref state.LubeBurst, null)?.Cancel();
    }

    private async Task FireLubeLauncherManyAsync(int count)
    {
        var state = Cur;
        var source = new CancellationTokenSource();
        Interlocked.Exchange(ref state.LubeBurst, source)?.Cancel();
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
            Interlocked.CompareExchange(ref state.LubeBurst, null, source);
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
    public void HismithSurprise()
    {
        if (!RejectedByLock("the surprise"))
            _ = HismithSurpriseAsync();
    }

    // "No, something else": the NEXT draw is made and put into effect only after HismithSurpriseDelaySeconds
    // (default 120 s), counted from the command; until then the current surprise stays. Exception: while mode A
    // runs the next draw is made at once. Shots of the previous
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

    // Hold ("utrzymaj to"): for HismithLockMinutes (default 5) the machine keeps whatever it does now. Every
    // control of the machine (modes, surprise, boost, speed / depth, sequence, keys) is ignored, except Stop
    // ("koniec", the safe word), which always works and is the only thing that releases the hold early. The Lube
    // Launcher is never affected. Saying it again restarts the time. A pending surprise draw is dropped when the
    // hold starts.
    // While the funscript drives the machine (no built-in mode), the hold also loops the last
    // HismithHoldLoopSeconds (default 20) of the script, so that "keep this" keeps the movement that was just
    // playing; the loop is cleared when the hold ends.
    private CancellationTokenSource _lockSource;
    private volatile int _lockMinutes = 5;
    private double _lockRemaining;
    private bool _holdLoopActive;
    private int _holdLoopSeconds = 20;

    public int HismithHoldLoopSeconds
    {
        get => _holdLoopSeconds;
        set => SetAndNotify(ref _holdLoopSeconds, Math.Clamp(value, 1, 600));
    }

    private void EndHoldLoop()
    {
        if (!_holdLoopActive)
            return;

        _holdLoopActive = false;
        EventAggregator.Publish(new ScriptLoopMessage(ScriptLoopCommand.Clear));
    }

    public int HismithLockMinutes
    {
        get => _lockMinutes;
        set => SetAndNotify(ref _lockMinutes, Math.Clamp(value, 1, 60));
    }

    public bool HismithLocked => _lockRemaining > 0;

    public void HismithLockDefault() => HismithLockStart(_lockMinutes);

    public void HismithLockStart(int minutes)
    {
        var seconds = (minutes > 0 ? Math.Clamp(minutes, 1, 60) : _lockMinutes) * 60;
        EndSurprise();
        var source = new CancellationTokenSource();
        Interlocked.Exchange(ref _lockSource, source)?.Cancel();
        if (Cur.Mode == 0)
        {
            _holdLoopActive = true;   // the funscript is running the machine: keep the last seconds of it going round
            EventAggregator.Publish(new ScriptLoopMessage(ScriptLoopCommand.Start, _holdLoopSeconds));
        }

        _ = RunLockAsync(source, seconds);
    }

    private void HismithLockClear()
    {
        Interlocked.Exchange(ref _lockSource, null)?.Cancel();
        if (_lockRemaining > 0)
            Logger.Info("Hold released");
        _lockRemaining = 0;
        EndHoldLoop();
        NotifyOfPropertyChange(nameof(HismithLocked));
        NotifyOfPropertyChange(nameof(HismithModeStatus));
    }

    private async Task RunLockAsync(CancellationTokenSource source, int seconds)
    {
        try
        {
            _lockRemaining = seconds;
            Logger.Info("Hold for {0} s: the machine's controls are ignored (Lube Launcher and Stop still work)", seconds);
            NotifyOfPropertyChange(nameof(HismithLocked));
            while (_lockRemaining > 0)
            {
                NotifyOfPropertyChange(nameof(HismithModeStatus));
                await Task.Delay(1000, source.Token);
                _lockRemaining -= 1;
            }

            _lockRemaining = 0;
            Interlocked.CompareExchange(ref _lockSource, null, source);
            EndHoldLoop();
            Logger.Info("Hold ended");
            NotifyOfPropertyChange(nameof(HismithLocked));
            NotifyOfPropertyChange(nameof(HismithModeStatus));
        }
        catch (OperationCanceledException) { }
    }

    private bool RejectedByLock(string what)
    {
        if (!HismithLocked)
            return false;

        Logger.Info("Held for another {0:0} s, ignoring {1}", _lockRemaining, what);
        Execute.PostToUIThread(() => NotifyOfPropertyChange(nameof(HismithModeStatus)));
        return true;
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
        if (RejectedByLock("the new draw"))
            return;
        if (!_surpriseActive)
        {
            Logger.Info("No surprise in force, nothing to draw again");
            return;
        }

        CancelLubeBurst();
        if (Cur.Mode == ModeA)
        {
            // mode A is the "all out" mode: no waiting, the next draw is made at once
            Interlocked.Exchange(ref _surpriseTimerSource, null)?.Cancel();
            _surpriseRemaining = 0;
            NotifyOfPropertyChange(nameof(HismithModeStatus));
            HismithSurprise();
            return;
        }

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
            if (!HismithLocked)
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
            HismithModeRandomCore();
        NotifyOfPropertyChange(nameof(HismithModeStatus));
        if (shots > 0)
            await FireLubeLauncherManyAsync(shots);
    }

    // Random mode only: one of the built-in modes 1-8 or the custom A / B, never the one that runs now.
    public void HismithModeRandom() => ForEachSide(HismithModeRandomCore);

    private void HismithModeRandomCore()
    {
        var current = Cur.Mode;
        var choices = Enumerable.Range(1, ModeB).Where(m => m != current).ToList();
        SetHismithModeCore(choices[Random.Shared.Next(choices.Count)], fromSurprise: true);
    }

    public void SetHismithMode(int mode) => ForEachSide(() => SetHismithModeCore(mode, fromSurprise: false));

    private void SetHismithModeCore(int mode, bool fromSurprise)
    {
        mode = Math.Clamp(mode, 0, ModeCalm);
        if (mode == ModeHalt)
            HismithLockClear();    // Stop always works and releases the hold
        else if (!fromSurprise && RejectedByLock($"mode {mode}"))
            return;

        if (!fromSurprise)
            EndSurprise();         // a mode chosen by hand ends the surprise and any pending draw

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
        Cur.Speed = Math.Clamp(speed, 0, 100);
        NotifyOfPropertyChange(nameof(HismithSpeed));
        await SendQuickActuatorsAsync(SpeedDescription, Cur.Speed, 100, ActuatorType.Oscillate);
    }

    private async Task ApplyHismithModeAsync(int mode)
    {
        await _modeGate.WaitAsync();
        try
        {
            // the old time-based sequence is replaced by an explicit mode, and an explicit mode by the sequence
            StopHismithSequence(sendToDevice: false);
            var state = Cur;
            var previous = state.Mode;

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
                state.Mode = 0;
                NotifyModeChanged();
                return;
            }

            if (mode == ModeHalt)
            {
                // stop: the script is held back for the machine and the speed goes to 0 at once
                state.Mode = mode;
                NotifyModeChanged();
                CancelLubeBurst();
                EventAggregator.Publish(new ScriptLoopMessage(ScriptLoopCommand.Clear));
                await SetSpeedAbsoluteAsync(0);
                NotifyOfPropertyChange(nameof(HismithModeStatus));
                return;
            }

            state.Mode = mode;              // the script output to the machine is suppressed from here on
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
                state.Depth = 50;
                NotifyOfPropertyChange(nameof(HismithDepth));
                await SendQuickActuatorsAsync(DepthDescription, 50, 100);
                await SetSpeedAbsoluteAsync(_hismithSlowestSpeed);
            }
            else
            {
                // A and B: the smallest stroke at full depth, A at full speed, B as slow as possible
                await SendQuickActuatorsAsync(WidthDescription, SmallestStrokeWidth, 100);
                state.Depth = 100;
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
        s.RegisterAction<int>($"{Identifier}::Hismith::Lock::Start", s => s.WithLabel("Hold the machine as it is (minutes, 0 = default); only Stop releases it"), HismithLockStart);
        s.RegisterAction($"{Identifier}::Hismith::Surprise::Again", HismithSurpriseAgain);
        s.RegisterAction($"{Identifier}::Hismith::Halt", () => SetHismithMode(ModeHalt));
        s.RegisterAction($"{Identifier}::Hismith::Calm", () => SetHismithMode(ModeCalm));
        RegisterHismithSideActions(s);
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
        s.UnregisterAction($"{Identifier}::Hismith::Lock::Start");
        s.UnregisterAction($"{Identifier}::Hismith::Surprise::Again");
        s.UnregisterAction($"{Identifier}::Hismith::Halt");
        s.UnregisterAction($"{Identifier}::Hismith::Calm");
        s.UnregisterAction($"{Identifier}::Hismith::Speed::Faster");
        s.UnregisterAction($"{Identifier}::Hismith::Speed::Slower");
        s.UnregisterAction($"{Identifier}::Hismith::Depth::Deeper");
        s.UnregisterAction($"{Identifier}::Hismith::Depth::Shallower");
        s.UnregisterAction($"{Identifier}::Hismith::Boost::Reset");
        s.UnregisterAction($"{Identifier}::Hismith::LubeLauncher::FireMany");
        UnregisterHismithSideActions(s);
    }

    private void SaveHismithModeSettings(JObject settings)
    {
        settings[nameof(HismithSpeedStep)] = HismithSpeedStep;
        settings[nameof(HismithDepthStep)] = HismithDepthStep;
        settings[nameof(HismithSlowestSpeed)] = HismithSlowestSpeed;
        settings[nameof(HismithSurpriseDelaySeconds)] = HismithSurpriseDelaySeconds;
        settings[nameof(HismithLockMinutes)] = HismithLockMinutes;
        settings[nameof(HismithHoldLoopSeconds)] = HismithHoldLoopSeconds;
        SaveHismithSideSettings(settings);
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
        if (settings.TryGetValue<int>(nameof(HismithLockMinutes), out var lockMinutes))
            HismithLockMinutes = lockMinutes;
        if (settings.TryGetValue<int>(nameof(HismithHoldLoopSeconds), out var holdLoop))
            HismithHoldLoopSeconds = holdLoop;
        LoadHismithSideSettings(settings);
    }

    public int HismithSlowestSpeed
    {
        get => _hismithSlowestSpeed;
        set => SetAndNotify(ref _hismithSlowestSpeed, Math.Clamp(value, 1, 100));
    }

    private void ResetHismithMode()
    {
        HismithLockClear();
        EndSurprise();
        foreach (var state in _sideStates.Values)
        {
            state.Mode = 0;
            state.SpeedBoost = 0;
            state.DepthBoost = 0;
        }
        NotifyModeChanged();
    }
}
