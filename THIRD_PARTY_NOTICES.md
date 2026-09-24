# Third-party notices

Cairn is built on the shoulders of excellent open-source projects. They are used as dependencies
and are not modified. Their licences require these notices to be preserved.

| Component | Used for | Licence | Copyright |
|---|---|---|---|
| [Spec Kit](https://github.com/github/spec-kit) | Spec workflow (Specs layer) | MIT | © GitHub, Inc. |
| [graphify](https://github.com/Graphify-Labs/graphify) | Code & doc graph extraction (Map layer) | MIT | © Graphify Labs |
| [Graphiti](https://github.com/getzep/graphiti) | Temporal fact graph (Timeline layer, deep tier) | Apache-2.0 | © Zep Software, Inc. |
| [Mem0](https://github.com/mem0ai/mem0) | Semantic memory (Memory layer, deep tier) | Apache-2.0 | © Mem0 |
| [claude-mem](https://github.com/thedotmack/claude-mem) | Agent session capture (Sessions layer) | Apache-2.0 | © Alex Newman |

Notes

- claude-mem's `ragtime/` directory is separately licensed (PolyForm Noncommercial 1.0.0).
  Cairn does not use, bundle or invoke it.
- Full licence texts are distributed with each package. Apache-2.0 components may include NOTICE
  files, which are reproduced by their packages and must be kept in redistributions.
