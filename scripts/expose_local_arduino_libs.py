from pathlib import Path

Import("env")

if env.IsIntegrationDump():
    Return()

project_dir = Path(env.subst("$PROJECT_DIR"))
framework_libraries = (
    project_dir
    / ".pio-local"
    / "framework-arduinoespressif32"
    / "libraries"
)

required = {
    "Networking": framework_libraries / "Network" / "src",
    "WiFi": framework_libraries / "WiFi" / "src",
    "NetworkClientSecure": framework_libraries / "NetworkClientSecure" / "src",
    "Preferences": framework_libraries / "Preferences" / "src",
    "Wire": framework_libraries / "Wire" / "src",
    "SPI": framework_libraries / "SPI" / "src",
}

missing = [f"{name}: {path}" for name, path in required.items() if not path.is_dir()]
if missing:
    raise RuntimeError(
        "A4.4.8 local Arduino library path missing:\n  "
        + "\n  ".join(missing)
    )

# This is deliberately a global CPPPATH. The local Arduino framework package
# is not being handled by the stock PlatformIO framework package pipeline,
# so sibling built-in libraries do not automatically receive each other's
# include directories during library compilation.
#
# Example layout:
#   WiFiGeneric.h -> #include "Network.h"
# while Network/src was not in the compiler include set.
env.AppendUnique(CPPPATH=[str(path) for path in required.values()])

print("[ARDUINO-LIBS] global CPPPATH enabled:")
for name, path in required.items():
    print(f"[ARDUINO-LIBS]   {name}: {path}")
