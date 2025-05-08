{
  mkShell,
  mypy,
  ruff,
  vm-spawner,
  custom_treefmt,
  ...
}:
mkShell {
  buildInputs = [
    mypy
    ruff
    custom_treefmt
  ] ++ vm-spawner.propagatedBuildInputs;

  shellHook = ''
    export GIT_ROOT="$(git rev-parse --show-toplevel)"
    export PKG_ROOT="$GIT_ROOT/pkgs/vm-spawner"
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
