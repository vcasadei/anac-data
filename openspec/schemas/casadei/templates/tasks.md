## 1. <!-- Task Group Name -->

<!-- Every task states how to verify it: a test, a command, an observable
     behavior, or a delivered artifact. -->
- [ ] 1.1 <!-- Task description and how it is verified -->
- [ ] 1.2 <!-- Task description and how it is verified -->

## 2. <!-- Task Group Name -->

- [ ] 2.1 <!-- Task description and how it is verified -->
- [ ] 2.2 <!-- Task description and how it is verified -->

## 3. Tests

<!-- Not optional. At least one automated test per feature or fix - unit,
     integration, or contract. Map tasks onto the BDD scenarios in the specs so
     coverage of the contract is traceable. -->
- [ ] 3.1 <!-- Test covering scenario: <scenario name> -->
- [ ] 3.2 <!-- Verify coverage threshold is maintained or raised -->

## 4. Migration

<!-- Delete this group if the change alters no database schema.
     One task per expand-and-contract step, plus rollback verification. -->
- [ ] 4.1 <!-- Expand: add new shape, dual-write -->
- [ ] 4.2 <!-- Migrate existing data -->
- [ ] 4.3 <!-- Contract: drop old shape -->
- [ ] 4.4 <!-- Run the `down` migration and verify it restores the prior shape -->

## 5. Documentation

<!-- Delete if the change alters no behavior, configuration, or public
     interface. Ships in the same commit/MR as the implementation. PT-BR by
     default; tables and technical specs over prose. -->
- [ ] 5.1 <!-- Update /docs/<file>.md -->

## 6. Quality Gates

<!-- Name the project's actual commands rather than describing them. -->
- [ ] 6.1 <!-- Build compiles cleanly: <command> -->
- [ ] 6.2 <!-- Linter and static analysis pass: <command> -->
- [ ] 6.3 <!-- Strict type checking passes: <command> -->
