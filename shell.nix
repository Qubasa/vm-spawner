{
  mkShell,
  mypy,
  ruff,
  vm-spawner,
  ...
}:
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

    # Add bin folder to PATH
    export PATH="$PKG_ROOT/bin":"$PATH"

    if [ -f .local.env ]; then
      source .local.env
    fi
  '';
}
