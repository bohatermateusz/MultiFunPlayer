# Buttplug.io output with the HISMITH S2 fork

The Buttplug.io output target now shows each actuator's feature description next to its index
(for example `3 - Position (move to depth now)`), both in the actuator picker and in the list of
configured mappings. Without it, devices with several actuators of the same type (such as the four
`Position` actuators of the HISMITH S2 in the Buttplug fork) cannot be told apart.

Saved mappings gain an `ActuatorDescription` field; older settings without it keep working and
just show no description.

Not compiled or run: this change was written without a .NET SDK. Build it on Windows first.
