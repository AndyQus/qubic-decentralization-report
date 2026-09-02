# Self-reporting registry

This is the **declared** layer of the report: operators/pools state which computor
identities they control. It is the anti-Sybil technique CFB pointed to — used "to the
fullest" by making the declarations public, versioned, and auditable.

## How to contribute (pools)

1. Add or edit your entry in `pools.json`:

   ```jsonc
   {
     "id": "your-slug",
     "label": "Your Pool",
     "url": "https://...",
     "source": "where this declaration is published",
     "verified": false,
     "computors": ["<60-char identity>", "..."]
   }
   ```

2. Run the validator:

   ```
   python scripts/validate_registry.py --epoch <current-epoch>
   ```

   It checks the IDs are well-formed, that no identity is claimed by two operators, and
   (with a live RPC) that each identity really is a computor that epoch.

3. Open a pull request. Git history is the audit trail — that history is itself part of
   the anti-Sybil value.

## Notes

- `computors` lists start **empty** on purpose. We never guess which slots belong to whom;
  an entry only carries identities once backed by a real declaration.
- `verified` flips to `true` when the declaration is corroborated (a signed statement, or
  matching on-chain payout linkage).
- The report clusters declared identities under their operator; everything undeclared is
  shown as "unattributed". The gap between declared and on-chain-detected clusters is the
  headline signal — see `docs/CONCEPT.md` §4.2.
