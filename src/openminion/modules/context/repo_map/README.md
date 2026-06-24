# RMP repo-map subpackage

Shape: `interfaces` + `schemas` + `parser` + `ranker` + `serializer` +
`cache` + `config` + `constants`.

Aider-style symbol map shipped as an optional pinned-prefix component
for coding-profile turns.  Parser is Python-only by default via `ast`;
multi-language extensions can register additional `RepoMapBuilder`
impls.
