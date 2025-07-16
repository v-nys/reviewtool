{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  inputs.poetry2nix.url = "github:nix-community/poetry2nix";

  outputs =
    {
      self,
      nixpkgs,
      poetry2nix,
    }:
    let
      supportedSystems = [
        "x86_64-linux"
        "x86_64-darwin"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      pkgs = forAllSystems (system: nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (
        system:
        let
          python = pkgs.${system}.python312;
          inherit
            (poetry2nix.lib.mkPoetry2Nix {
              pkgs = pkgs.${system};
            })
            mkPoetryApplication
            ;
        in
        {
          default = mkPoetryApplication {
            projectDir = self;
            python = python;
            dependencies = [
              pkgs.${system}.python312Packages.textual-image
            ];
          };
        }
      );

      devShells = forAllSystems (
        system:
        let
          python = pkgs.${system}.python312;
          inherit
            (poetry2nix.lib.mkPoetry2Nix {
              pkgs = pkgs.${system};
            })
            mkPoetryEnv
            ;
        in
        {
          default = pkgs.${system}.mkShellNoCC {
            packages = with pkgs.${system}; [
              (mkPoetryEnv {
                projectDir = self;
                python = python;
              })
              python312Packages.textual-image
              poetry
            ];
          };
        }
      );
    };
}
