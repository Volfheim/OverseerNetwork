# Third-Party Notices

The root MIT license covers Overseer code, not third-party marks or dependencies.

## Bundled Resources

- `static/icons/`: Lucide, ISC with Feather attribution; complete license included
  at `static/icons/LICENSE`. Source: https://github.com/lucide-icons/lucide.
- `static/img/three-globe/`: unmodified images from the MIT-licensed three-globe
  repository, commit `017a3a5d182b2413f403154d3eb3ed4af3e598ca`.
  Source: https://github.com/vasturiano/three-globe/tree/017a3a5d182b2413f403154d3eb3ed4af3e598ca/example/img.
  Copyright (c) 2019 Vasco Asturiano. Full upstream license is included in that
  directory. These assets are used without the three-globe library itself.

| Texture | SHA256 |
|---|---|
| earth-blue-marble.jpg | 228deba2e4b600146bdcb6cfa359b8ead6aacc2b1c13550a29cd82824cfa1c01 |
| earth-topology.png | 839b12da2e4dd346b256cebae72e10c479a102c8980a22084c41275e4b9a0e12 |
| earth-water.png | 3a8132db56aac4e64e7fcbf2928ee970c075ac6a96b38798d0bb8be82836a4d3 |

Terminal sounds are synthesized by the Web Audio API; no game audio is distributed.
Old local audio/texture files are excluded from the source bundle and Git until
their provenance is established. The generic application icon is project artwork.

## Browser Dependencies

Loaded remotely rather than bundled: Three.js r128 (MIT, https://github.com/mrdoob/three.js),
xterm 5.3.0 and fit/web-links addons (MIT, https://github.com/xtermjs/xterm.js).
Google Fonts supplies VT323 (SIL Open Font License) and Ubuntu Mono (Ubuntu Font
License). Their license files remain with upstream distributions; offline
vendoring must include the respective notices.

Python dependencies retain their own licenses in installed distributions.

## Theme Names

Fallout, Vault-Tec and RobCo references describe an unofficial fan-inspired theme.
They are not licensed under this project's MIT license. No affiliation or
endorsement is claimed. The neutral operations theme is also available.
