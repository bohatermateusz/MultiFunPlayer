using Buttplug;
using MultiFunPlayer.Common;
using MultiFunPlayer.Shortcut;
using Newtonsoft.Json.Linq;

namespace MultiFunPlayer.OutputTarget.ViewModels;

// One switch moves the HISMITH S2 between the rear and the front, live, while the film plays (user, 2026-11-20): its
// map rows take the other side's scripts (rear_speed / rear_width / rear_depth on R1 / L1 / R0 <-> suck_speed /
// suck_width / suck_depth on A1 / L2 / R2, MachineSideAxes) and the device takes that side's role, so that the voice
// commands for "front" / "rear" reach it. The pairs are a setting; the state is kept with the map and the roles.
internal sealed partial class ButtplugOutputTarget
{
    private Dictionary<DeviceAxis, DeviceAxis> _machineSideAxes = new()
    {
        [DeviceAxis.Parse("R1")] = DeviceAxis.Parse("A1"),
        [DeviceAxis.Parse("L1")] = DeviceAxis.Parse("L2"),
        [DeviceAxis.Parse("R0")] = DeviceAxis.Parse("R2"),
    };

    private HismithSide? _machineSide;    // set once the switch was used: the side the machine serves (kept on reconnect)

    public bool HasMachineSideSwitch => AvailableDevices.ToList().Any(IsMachine);
    public bool MachineIsFront => MachineDevice() is { } d && RoleOf(d) == HismithSide.Front;
    // the button says what a click does, not where the machine is now (user, 2026-11-20)
    public string MachineSideLabel => MachineIsFront ? "S2 \u2192 REAR (from behind)" : "S2 \u2192 FRONT (mouth)";

    private bool IsMachine(ButtplugDevice device)
        => device.ScalarActuators.Any(a => a.ActuatorType == ActuatorType.Position
                                         && a.FeatureDescriptor?.StartsWith(PresetModeDescription, StringComparison.OrdinalIgnoreCase) == true);

    private ButtplugDevice MachineDevice() => AvailableDevices.ToList().FirstOrDefault(IsMachine);

    public void ToggleMachineSide() => SetMachineSide(MachineIsFront ? HismithSide.Rear : HismithSide.Front);

    public void SetMachineSide(HismithSide side)
    {
        var device = MachineDevice();
        if (device == null || side == HismithSide.Both)
            return;

        var toFront = side == HismithSide.Front;
        var swap = toFront ? _machineSideAxes : _machineSideAxes.ToDictionary(kv => kv.Value, kv => kv.Key);
        var moved = 0;
        foreach (var row in GetSettingsForDevice(device).ToList())
        {
            if (!swap.TryGetValue(row.SourceAxis, out var axis))
                continue;
            row.SourceAxis = axis;
            moved++;
        }

        _machineSide = side;
        _deviceRoles[DeviceKey(device)] = side;
        RefreshDeviceRoles();
        _forceResend = true;
        Logger.Info("The machine \"{0}\" now serves the {1} side ({2} map row(s) moved to its scripts)", DeviceKey(device), side, moved);
        NotifyOfPropertyChange(nameof(MachineIsFront));
        NotifyOfPropertyChange(nameof(MachineSideLabel));
        NotifyOfPropertyChange(nameof(HismithModeStatus));
    }

    // on (re)connect the switch wins over the engine's display name ("Hismith S2 Rear")
    private bool ApplyMachineSide(ButtplugDevice device)
    {
        if (_machineSide is not { } side || !IsMachine(device))
            return false;
        _deviceRoles[DeviceKey(device)] = side;
        return true;
    }

    private void RegisterMachineSideActions(IShortcutManager s)
    {
        s.RegisterAction($"{Identifier}::Hismith::MachineSide::Toggle", ToggleMachineSide);
        s.RegisterAction<string>($"{Identifier}::Hismith::MachineSide::Set", x => x.WithLabel("front / rear"),
            side => SetMachineSide(string.Equals(side, "front", StringComparison.OrdinalIgnoreCase) ? HismithSide.Front : HismithSide.Rear));
    }

    private void UnregisterMachineSideActions(IShortcutManager s)
    {
        s.UnregisterAction($"{Identifier}::Hismith::MachineSide::Toggle");
        s.UnregisterAction($"{Identifier}::Hismith::MachineSide::Set");
    }

    private void SaveMachineSideSettings(JObject settings)
    {
        settings["MachineSideAxes"] = JObject.FromObject(_machineSideAxes.ToDictionary(kv => kv.Key.Name, kv => kv.Value.Name));
        if (_machineSide is { } side)
            settings["MachineSide"] = side.ToString();
    }

    private void LoadMachineSideSettings(JObject settings)
    {
        if (settings["MachineSideAxes"] is JObject axes)
        {
            var map = new Dictionary<DeviceAxis, DeviceAxis>();
            foreach (var (from, to) in axes)
                if (DeviceAxis.TryParse(from, out var a) && DeviceAxis.TryParse(to?.ToString(), out var b))
                    map[a] = b;
            if (map.Count > 0)
                _machineSideAxes = map;
        }

        if (Enum.TryParse<HismithSide>(settings["MachineSide"]?.ToString(), out var side))
            _machineSide = side;
    }
}
