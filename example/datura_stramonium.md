# Example: thorn apple (*Datura stramonium*) in both directions

Thorn apple (taxid 4076) is poisonous through its tropane alkaloids scopolamine, hyoscyamine and atropine
(racemic hyoscyamine). They block muscarinic acetylcholine receptors. This example follows the ChEMBL ↔ nr joins
(`nr_join.py`, endpoints `/nr/...`, form `web/nr.php`) from the toxin to its targets and from the plant's own
proteins back to ChEMBL. Data: ChEMBL 37, NCBI nr as of 2026-11-10 (1.17 billion sequences).

## Forward: toxin → targets → nr

`GET /nr/compounds/scopolamine` (form: *Compound/toxin → target proteins in nr*)

| compound | target components | in nr | curated mechanism (drug_mechanism) |
|---|---:|---:|---|
| scopolamine | 24 | 23 | muscarinic receptor M1 |
| hyoscyamine | 27 | 27 | – (listed under atropine) |
| atropine | 31 | 28 | muscarinic receptors M1, M2, M3 |

Each target row carries its nr entry, the number of nr entries with exactly that sequence, the number of
organisms among them, and a link to a DIAMOND search for related proteins:

| target | UniProt | nr entry | length | identical entries | organisms |
|---|---|---|---:|---:|---:|
| muscarinic acetylcholine receptor M1, human | P11229 | P11229.2 | 460 | 21 | 14 |
| muscarinic acetylcholine receptor M2, human | P08172 | P08172.1 | 466 | 29 | 7 |
| muscarinic acetylcholine receptor M3, human | P20309 | P20309.1 | 590 | 59 | 3 |

The human M1 sequence is identical in 14 taxa (other primates): `GET /nr/organisms/{oid}` lists them.
The DIAMOND link (`https://blast.heissa.de/?acc=P11229.2`) finds the receptor in insects, nematodes and
other animals, i.e. the organisms in which the toxin can act on a related protein.

Each query took 0.4 s.

## Reverse: the plant's proteins → ChEMBL

`GET /nr/proteins/{acc}` (form: *nr protein → ChEMBL*), run for all thorn apple proteins in nr:

- nr holds **52 510 accessions (52 298 sequences)** of *Datura stramonium*; 12 of them have a UniProt accession.
- **None of them is a ChEMBL target.** ChEMBL knows the plant only as a whole-organism target
  (CHEMBL613964, assays on extracts).
- The enzymes that make the toxin are in nr, for example:

| nr entry | protein | ChEMBL targets |
|---|---|---:|
| P50162.1 | tropinone reductase I | 0 |
| P50163.1 | tropinone reductase II | 0 |
| Q70EW6.1 | putrescine N-methyltransferase | 0 |
| A0A9Y1E1K8.1 | tropinone synthase (cytochrome P450) | 0 |
| ALD59774.1 | hyoscyamine 6β-hydroxylase | 0 |

## Known limit

ChEMBL lists the proteins a compound acts **on** (almost only human, laboratory animal and pathogen proteins),
not the proteins of the organism that **makes** the toxin. Reverse joins by identical sequence therefore find
something for human, parasite, bacterial and viral proteins, but not for a toxic plant or animal itself.
For those, the way back is a homolog search: a DIAMOND search with the plant protein against nr, then
`/nr/proteins/{hit}` for hits that are drug targets.

Search for "Scopolamin" (German) finds nothing: ChEMBL names and synonyms are English (scopolamine,
hyoscine); the form offers the closest names.
