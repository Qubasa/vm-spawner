{
  mkShell,
  mypy,
  ruff,
  vm-spawner,
  ...
}:
let
  baseImageUrl = "https://static.clan.lol/images/nixos-installer-x86_64-linux.iso";
  baseImageChecksum = "1g8gkgy0w0labpcv4sxmdf0j6g1ik2zgnvb1b4izyz464r7nsrdc";
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
