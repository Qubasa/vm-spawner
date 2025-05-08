{
  python3Packages,
  opentofu,
  cdrtools,
  ...
}:

python3Packages.buildPythonApplication {
  name = "vm-spawner";
  src = ./.;
  format = "pyproject";

  makeWrapperArgs = [
  ];

  pythonImportsCheck = [ "vm_spawner" ];

  build-system = with python3Packages; [ setuptools ];
  propagatedBuildInputs = [
    opentofu
    cdrtools
    python3Packages.libvirt
  ];
}
