{
  description = "SyncWatch – synced peer-to-peer video watching";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }:
    let
      forAll = f: nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ]
        (s: f nixpkgs.legacyPackages.${s});
    in {
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [ ps.pyside6 ps.mpv ps.yt-dlp ]))
            pkgs.deno # JS runtime yt-dlp needs for YouTube
          ];
        };
      });
      packages = forAll (pkgs: {
        default = pkgs.python3Packages.buildPythonApplication {
          pname = "syncwatch";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = with pkgs.python3Packages; [ pyside6 mpv yt-dlp ];
          pythonRemoveDeps = [ "python-mpv" ]; # nixpkgs names it "mpv"
          makeWrapperArgs = [ "--prefix" "PATH" ":" (pkgs.lib.makeBinPath [ pkgs.deno ]) ];
          doCheck = false;
        };
      });
    };
}
