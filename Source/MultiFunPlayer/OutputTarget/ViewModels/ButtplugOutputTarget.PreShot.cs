using Buttplug;
using MultiFunPlayer.Common;
using System.Diagnostics;

namespace MultiFunPlayer.OutputTarget.ViewModels;

// Before a shot of the FRONT Lube Launcher, the front machine (a Vibrate device of the front side, e.g. the ESP32
// "Lovense Lush") sometimes (PreShotChance) speeds up gradually over PreShotSeconds - not to the top, to a random
// level above what it does - and slides back to its script after the shot (PreShotEaseBack seconds). Script shots are seen PreShotSeconds
// ahead in the launcher's track; a shot asked for (voice, key, surprise) waits those seconds when the ramp is drawn.
// Without a front Vibrate device (the Lovense is not always connected) nothing changes and nothing waits.
internal sealed partial class ButtplugOutputTarget
{
    private const double PreShotSeconds = 5.0;
    private const double PreShotChance = 0.5;
    private const double PreShotHoldAfter = 0.6;     // the ramp level stays this long after the (last) shot
    private const double PreShotEaseBack = 3.0;      // then it slides back to the script over this many seconds

    private readonly object _rampLock = new();
    private double _rampStart, _rampShot, _rampHoldEnd, _rampEnd = double.NegativeInfinity;
    private double _rampTarget;
    private readonly Dictionary<DeviceAxis, double> _rampFrom = [];
    private double _plannedScriptShot = double.NegativeInfinity;

    private static double Now => Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency;

    private bool IsFrontDevice(ButtplugDevice device) => device != null && RoleOf(device) == HismithSide.Front;

    // the axes that drive a Vibrate actuator of a front device (the Lovense's A1)
    private List<DeviceAxis> FrontVibrateAxes()
        => [.. DeviceSettings.ToList()
                .Where(s => s.ActuatorType == ActuatorType.Vibrate && IsFrontDevice(GetDeviceFromSettings(s)))
                .Select(s => s.SourceAxis).Distinct()];

    // the axes whose track fires a front launcher (A0 = valve)
    private List<DeviceAxis> FrontSprayAxes()
        => [.. DeviceSettings.ToList()
                .Where(s => s.ActuatorType == ActuatorType.Position && GetDeviceFromSettings(s) is { } d && IsFrontDevice(d)
                         && d.TryGetActuator(s.ActuatorIndex, s.ActuatorType, out var a) && a is ButtplugDeviceScalarActuator sa
                         && sa.FeatureDescriptor?.StartsWith(SprayDescription, StringComparison.OrdinalIgnoreCase) == true)
                .Select(s => s.SourceAxis).Distinct()];

    private void StartPreShotRamp(double secondsToShot, double holdAfter)
    {
        lock (_rampLock)
        {
            var now = Now;
            _rampFrom.Clear();
            foreach (var axis in FrontVibrateAxes())
                _rampFrom[axis] = GetValue(axis);
            var from = _rampFrom.Count > 0 ? _rampFrom.Values.Max() : 0;
            // gradually up, not to the top: a random level between 60 and 85 %, at least a bit above the script
            var target = 0.60 + Random.Shared.NextDouble() * 0.25;
            _rampTarget = Math.Min(0.95, Math.Max(target, from + 0.15));
            _rampStart = now;
            _rampShot = now + secondsToShot;
            _rampHoldEnd = _rampShot + holdAfter;
            _rampEnd = _rampHoldEnd + PreShotEaseBack;
        }

        Logger.Info("Pre-shot ramp: front machine speeds up to {0:0}% over {1:0.0} s", _rampTarget * 100, secondsToShot);
    }

    // called every update tick with the script values: raises the front Vibrate axes while a ramp runs
    private void ApplyPreShotRamp(Dictionary<DeviceAxis, double> values)
    {
        try
        {
            WatchScriptShots();

            lock (_rampLock)
            {
                var now = Now;
                if (now > _rampEnd)
                    return;

                static double Smooth(double x) => x * x * (3 - 2 * x);    // smooth start and arrival
                var up = Smooth(Math.Clamp((now - _rampStart) / Math.Max(_rampShot - _rampStart, 0.1), 0, 1));
                var back = Smooth(Math.Clamp((now - _rampHoldEnd) / PreShotEaseBack, 0, 1));
                foreach (var (axis, from) in _rampFrom)
                {
                    if (!values.TryGetValue(axis, out var v) || !double.IsFinite(v))
                        continue;
                    // up from where the script was to the target, hold, then down to what the script does now
                    var level = from + (_rampTarget - from) * up;
                    level += (v - level) * back;
                    values[axis] = back > 0 ? level : Math.Max(v, level);
                }
            }
        }
        catch (Exception e)
        {
            Logger.Debug(e, "Pre-shot ramp exception");
        }
    }

    // a shot in the front launcher's track PreShotSeconds ahead: draw once per shot whether the front machine ramps up
    private void WatchScriptShots()
    {
        var sprayAxes = FrontSprayAxes();
        if (sprayAxes.Count == 0)
            return;

        foreach (var axis in sprayAxes)
        {
            var seconds = SecondsUntilAbove(axis, 0.5, PreShotSeconds);
            if (seconds is not { } s)
                continue;

            var shotAt = Now + s;
            if (Math.Abs(shotAt - _plannedScriptShot) < 1.5)
                return;                                     // this shot has been drawn already
            _plannedScriptShot = shotAt;

            if (FrontVibrateAxes().Count > 0 && Random.Shared.NextDouble() < PreShotChance)
                StartPreShotRamp(s, PreShotHoldAfter);
            return;
        }
    }

    // a shot asked for (voice, key, surprise): for a front launcher, half of the time the front machine ramps up first
    // and the shots wait for it; the level holds through a burst
    private async Task PreShotRampAsync(int count, CancellationToken token)
    {
        var frontLauncher = GetQuickActuators(SprayDescription).Any(a => AvailableDevices.ToList().Any(d => IsFrontDevice(d) && d.ScalarActuators.Contains(a)));
        if (!frontLauncher || FrontVibrateAxes().Count == 0 || Random.Shared.NextDouble() >= PreShotChance)
            return;

        StartPreShotRamp(PreShotSeconds, PreShotHoldAfter + (count - 1) * 1.2);
        await Task.Delay(TimeSpan.FromSeconds(PreShotSeconds), token);
    }
}
