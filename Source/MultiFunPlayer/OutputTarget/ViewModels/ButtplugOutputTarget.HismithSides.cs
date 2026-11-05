using Buttplug;
using MultiFunPlayer.Shortcut;
using Newtonsoft.Json.Linq;
using Stylet;
using System.Collections.Concurrent;
using System.Collections.ObjectModel;

namespace MultiFunPlayer.OutputTarget.ViewModels;

internal enum HismithSide
{
    Both = 0,
    Rear = 1,
    Front = 2,
}

// The state of one side: the machine taking her from behind (rear) and the one in her mouth (front) are driven
// separately - their own mode, speed, depth and boosts, their own Lube Launcher.
internal sealed class HismithSideState
{
    public volatile int Mode;           // 0 = funscripts, 1-8 = built-in mode, 9 = A, 10 = B, 11 = stop, 12 = calm
    public int SpeedBoost;
    public int DepthBoost;
    public int Depth = 50;
    public int Speed = 50;
    public CancellationTokenSource LubeBurst;
}

// One row of the device roles list in the view: a connected device and the side it serves.
internal sealed class HismithDeviceRole : PropertyChangedBase
{
    private readonly Action<HismithDeviceRole> _changed;
    private string _role;

    public HismithDeviceRole(string key, string label, HismithSide role, Action<HismithDeviceRole> changed)
    {
        Key = key;
        Label = label;
        _role = role.ToString();
        _changed = changed;
    }

    public string Key { get; }
    public string Label { get; }

    public string Role
    {
        get => _role;
        set
        {
            if (SetAndNotify(ref _role, value))
                _changed(this);
        }
    }
}

// Which device is which: every connected device has a role - Both (default: it follows every command, as before),
// Rear or Front. A command for a side reaches only the devices of that side (and the Both ones); a command without a
// side reaches every side, each with its own state. The role is remembered by the device's name and index.
internal sealed partial class ButtplugOutputTarget
{
    private readonly Dictionary<HismithSide, HismithSideState> _sideStates = new()
    {
        [HismithSide.Rear] = new HismithSideState(),
        [HismithSide.Front] = new HismithSideState(),
    };

    private static readonly AsyncLocal<HismithSide?> _sideScope = new();
    private readonly ConcurrentDictionary<string, HismithSide> _deviceRoles = new(StringComparer.OrdinalIgnoreCase);

    public IReadOnlyList<string> HismithSideNames { get; } = [nameof(HismithSide.Both), nameof(HismithSide.Rear), nameof(HismithSide.Front)];
    public ObservableCollection<HismithDeviceRole> DeviceRoles { get; } = [];
    public bool HasDeviceRoles => DeviceRoles.Count > 1;

    // the state the current command works on: the side in scope, the rear one outside any scope (the view)
    private HismithSideState Cur => _sideStates[_sideScope.Value == HismithSide.Front ? HismithSide.Front : HismithSide.Rear];

    private static string DeviceKey(ButtplugDevice device) => $"{device.Name}#{device.Index}";

    private HismithSide RoleOf(ButtplugDevice device)
        => device != null && _deviceRoles.TryGetValue(DeviceKey(device), out var role) ? role : HismithSide.Both;

    // the side whose state drives a device's script values (a "Both" device follows the rear one)
    private HismithSideState StateOf(ButtplugDevice device)
        => _sideStates[RoleOf(device) == HismithSide.Front ? HismithSide.Front : HismithSide.Rear];

    private bool InSideScope(ButtplugDevice device)
    {
        var scope = _sideScope.Value;
        if (scope is null or HismithSide.Both)
            return true;

        var role = RoleOf(device);
        return role == HismithSide.Both || role == scope;
    }

    // the sides that have a device of their own; none (no roles set) means one side for everything, as before
    private List<HismithSide> PresentSides()
    {
        var roles = AvailableDevices.ToList().Select(RoleOf).ToHashSet();
        var sides = new List<HismithSide>();
        if (roles.Contains(HismithSide.Rear) || roles.Contains(HismithSide.Both) || roles.Count == 0)
            sides.Add(HismithSide.Rear);
        if (roles.Contains(HismithSide.Front))
            sides.Add(HismithSide.Front);
        return sides;
    }

    private void InSide(HismithSide side, Action action)
    {
        var previous = _sideScope.Value;
        _sideScope.Value = side;
        try
        {
            action();
        }
        finally
        {
            _sideScope.Value = previous;
        }
    }

    // a command without a side: every side does it with its own state
    private void ForEachSide(Action action)
    {
        foreach (var side in PresentSides())
            InSide(side, action);
    }

    private void RefreshDeviceRoles()
    {
        Execute.OnUIThread(() =>
        {
            DeviceRoles.Clear();
            foreach (var device in AvailableDevices.ToList())
                DeviceRoles.Add(new HismithDeviceRole(DeviceKey(device), $"{device.Name} #{device.Index}", RoleOf(device), OnDeviceRoleChanged));
            NotifyOfPropertyChange(nameof(HasDeviceRoles));
        });
    }

    private void OnDeviceRoleChanged(HismithDeviceRole row)
    {
        if (!Enum.TryParse<HismithSide>(row.Role, out var role))
            return;

        _deviceRoles[row.Key] = role;
        Logger.Info("Device \"{0}\" serves the {1} side", row.Label, role);
        _forceResend = true;
        NotifyOfPropertyChange(nameof(HismithModeStatus));
    }

    private void RegisterHismithSideActions(IShortcutManager s)
    {
        s.RegisterAction($"{Identifier}::Hismith::Sides::Status", () => Logger.Info("Sides: {0}", HismithModeStatus));
        foreach (var side in new[] { HismithSide.Rear, HismithSide.Front })
        {
            var p = $"{Identifier}::Hismith::{side}";
            var sd = side;
            s.RegisterAction<int>($"{p}::Mode::Set", x => x.WithLabel($"{sd}: mode (0 = funscript, 1-8, 9 = A, 10 = B, 11 = stop, 12 = calm)"), mode => InSide(sd, () => SetHismithModeCore(mode, fromSurprise: false)));
            s.RegisterAction($"{p}::Mode::Script", () => InSide(sd, () => SetHismithModeCore(0, fromSurprise: false)));
            s.RegisterAction($"{p}::Mode::Random", () => InSide(sd, HismithModeRandomCore));
            s.RegisterAction($"{p}::Halt", () => InSide(sd, () => SetHismithModeCore(ModeHalt, fromSurprise: false)));
            s.RegisterAction($"{p}::Calm", () => InSide(sd, () => SetHismithModeCore(ModeCalm, fromSurprise: false)));
            s.RegisterAction($"{p}::Speed::Faster", () => InSide(sd, () => ChangeSpeed(HismithSpeedStep)));
            s.RegisterAction($"{p}::Speed::Slower", () => InSide(sd, () => ChangeSpeed(-HismithSpeedStep)));
            s.RegisterAction($"{p}::Depth::Deeper", () => InSide(sd, () => ChangeDepth(HismithDepthStep)));
            s.RegisterAction($"{p}::Depth::Shallower", () => InSide(sd, () => ChangeDepth(-HismithDepthStep)));
            s.RegisterAction($"{p}::Boost::Reset", () => InSide(sd, BoostResetCore));
            s.RegisterAction<int>($"{p}::Speed::Set", x => x.WithLabel($"{sd}: speed (0-100)"), speed => InSide(sd, () => HismithSpeed = speed));
            s.RegisterAction($"{p}::LubeLauncher::Fire", () => InSide(sd, () => _ = FireLubeLauncherAsync()));
            s.RegisterAction<int>($"{p}::LubeLauncher::FireMany", x => x.WithLabel($"{sd}: Lube Launcher shots (1-5)"), count => InSide(sd, () => _ = FireLubeLauncherManyAsync(Math.Clamp(count, 1, 5))));
        }
    }

    private void UnregisterHismithSideActions(IShortcutManager s)
    {
        s.UnregisterAction($"{Identifier}::Hismith::Sides::Status");
        foreach (var side in new[] { HismithSide.Rear, HismithSide.Front })
        {
            var p = $"{Identifier}::Hismith::{side}";
            foreach (var name in new[] { "Mode::Set", "Mode::Script", "Mode::Random", "Halt", "Calm", "Speed::Faster", "Speed::Slower",
                                         "Depth::Deeper", "Depth::Shallower", "Boost::Reset", "Speed::Set", "LubeLauncher::Fire", "LubeLauncher::FireMany" })
                s.UnregisterAction($"{p}::{name}");
        }
    }

    private void SaveHismithSideSettings(JObject settings)
        => settings["HismithDeviceRoles"] = JObject.FromObject(_deviceRoles.ToDictionary(kv => kv.Key, kv => kv.Value.ToString()));

    private void LoadHismithSideSettings(JObject settings)
    {
        if (settings["HismithDeviceRoles"] is not JObject roles)
            return;

        foreach (var (key, value) in roles)
            if (Enum.TryParse<HismithSide>(value?.ToString(), out var role))
                _deviceRoles[key] = role;
    }

    private string SideStatus(HismithSide side)
    {
        var st = _sideStates[side];
        var mode = st.Mode switch
        {
            0 => "funscript",
            ModeA => "A",
            ModeB => "B",
            ModeHalt => "STOPPED",
            ModeCalm => "CALM",
            _ => $"mode {st.Mode}"
        };
        return st.Mode == 0 ? $"{mode} (boost {st.SpeedBoost:+0;-0;0}/{st.DepthBoost:+0;-0;0})" : $"{mode} (speed {st.Speed}, depth {st.Depth})";
    }
}
