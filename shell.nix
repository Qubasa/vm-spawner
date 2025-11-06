{
  mkShell,
  mypy,
  ruff,
  vm-spawner,
  ...
}:
let
  baseImageUrl = "https://static.clan.lol/images/nixos-installer-x86_64-linux.iso";
  baseImageChecksum = "bc0671d86f07f8d90bfbab2ac968e685976956d6aedc9748ef07e2af3e799d7e";
  baseImage = builtins.fetchurl {
    url = baseImageUrl;
    sha256 = baseImageChecksum;
  };
in 
mkShell {
  buildInputs = [
    mypy
    ruff
  ] ++ vm-spawner.propagatedBuildInputs;

  shellHook = ''
    export GIT_ROOT="$(git rev-parse --show-toplevel)"
    export PKG_ROOT="$GIT_ROOT"
    export PYTHONWARNINGS=error

    # Add current package to PYTHONPATH
    export PYTHONPATH="$PKG_ROOT''${PYTHONPATH:+:$PYTHONPATH:}"

    export CLAN_BASE_IMAGE="${baseImage}"

    # Add bin folder to PATH
    export PATH="$PKG_ROOT/bin":"$PATH"

    if [ -f .local.env ]; then
      source .local.env
    fi
  '';
}
