{
  ...
}:
{
  perSystem =
    {
      pkgs,
      config,
      self',
      ...
    }:

    {
      packages.vm-spawner = pkgs.callPackage ./default.nix {
      };

      devShells.vm-spawner = pkgs.callPackage ./shell.nix {
        inherit (self'.packages) vm-spawner;
        # treefmt with config defined in ./flake-parts/formatting.nix
        custom_treefmt = config.treefmt.build.wrapper;
      };
    };
}
