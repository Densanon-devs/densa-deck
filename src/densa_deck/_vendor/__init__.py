"""Third-party Densanon packages carried inside Densa Deck.

Vendored rather than installed, so every shipped build carries them with no
install step and a frozen build picks them up like any other source package
(`collect_submodules("densa_deck")` in densa-deck.spec reaches them).

Never edit a vendored copy here. `densanon_hub/VENDORED.md` names the
upstream commit and the command that refreshes it.
"""
