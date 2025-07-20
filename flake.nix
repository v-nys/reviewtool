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
          p2nix = (
            poetry2nix.lib.mkPoetry2Nix {
              pkgs = pkgs.${system};
            }
          );
        in
        {
          default = p2nix.mkPoetryApplication {
            projectDir = self;
            python = python;
            overrides = p2nix.overrides.withDefaults (
              self: super: {
                textual-image = super.textual-image.overridePythonAttrs (old: {
                  buildInputs = (old.buildInputs or [ ]) ++ [ super.setuptools ];
                });
              }
            );
          };
        }
      );
      devShells = forAllSystems (
        system:
        let
          python = pkgs.${system}.python312;
          p2nix = (
            poetry2nix.lib.mkPoetry2Nix {
              pkgs = pkgs.${system};
            }
          );
        in
        {
          default = pkgs.${system}.mkShellNoCC {
            packages = with pkgs.${system}; [
              (p2nix.mkPoetryEnv {
                projectDir = self;
                python = python;
                overrides = p2nix.overrides.withDefaults (
                  self: super: {
                    textual-image = super.textual-image.overridePythonAttrs (old: {
                      buildInputs = (old.buildInputs or [ ]) ++ [ super.setuptools ];
                    });
                  }
                );

              })
              poetry
            ];
          };
        }
      );
    };
}
