<?php
/*
 * ChEMBL <-> NCBI nr: second query form of the ChEMBL query service (github.com/gerontec/ChEMBL).
 * Joins ChEMBL targets with the nr protein database of github.com/gerontec/blastDiamond by UniProt accession
 * (API endpoints /nr/...). Every protein links to a DIAMOND homolog search on the BLAST form.
 * Same setup as index.php: PHP + local Bootstrap, the API is called server side.
 */

declare(strict_types=1);

$ini = is_readable('/etc/chembl/web.ini') ? (parse_ini_file('/etc/chembl/web.ini') ?: []) : [];
$API = rtrim(getenv('CHEMBL_API') ?: ($ini['api'] ?? 'http://127.0.0.1:9099'), '/');

$lang = (($_GET['lang'] ?? '') === 'en' || (!isset($_GET['lang'])
        && !str_starts_with(strtolower($_SERVER['HTTP_ACCEPT_LANGUAGE'] ?? 'de'), 'de'))) ? 'en' : 'de';

$T = [
 'de' => [
  'title' => 'ChEMBL ↔ Proteindatenbank nr', 'back' => 'ChEMBL-Abfrage',
  'lead' => 'Verbindet Wirkstoffe und Gifte aus ChEMBL mit ihren Zielproteinen in der NCBI-Proteindatenbank nr (1,17 Mrd. Sequenzen) – und umgekehrt. Zu jedem Protein gibt es einen Link zur Suche nach verwandten Proteinen (DIAMOND).',
  'mode_compound' => 'Wirkstoff/Gift → Zielproteine in nr', 'mode_target' => 'Zielprotein → nr', 'mode_protein' => 'nr-Protein → ChEMBL',
  'ph_compound' => 'z. B. Scopolamin, Atropine, Tetrodotoxin, Ziconotide', 'ph_target' => 'z. B. CHRM1, P11229, CHEMBL216',
  'ph_protein' => 'nr-Accession, z. B. P35498, NP_000729.2, XP_…',
  'help_compound' => 'Name, Synonym oder ChEMBL-ID. Gezeigt werden die Zielproteine (kuratierter Wirkmechanismus zuerst) und ihr Eintrag in nr.',
  'help_target' => 'Gen-Symbol, UniProt-Accession, ChEMBL-ID oder genauer Name des Zielproteins.',
  'help_protein' => 'Beliebige Accession aus nr. Gesucht werden ChEMBL-Ziele mit identischer Sequenz; ähnliche Proteine findet die DIAMOND-Suche.',
  'search' => 'Suchen', 'human' => 'nur menschliche Zielproteine', 'organism' => 'Organismus (optional)',
  'notfound' => 'Nichts gefunden für', 'didyoumean' => 'Meinten Sie:', 'error' => 'Fehler bei der Abfrage', 'ratelimit' => 'Zu viele Anfragen – bitte eine Minute warten.',
  'unavailable' => 'Die nr-Datenbank ist gerade nicht erreichbar.',
  'results' => 'Zielproteine', 'excel' => 'Excel-Datei', 'none' => 'Keine Treffer.',
  'own_seq' => 'Eigene Sequenz des Wirkstoffs (Peptid/Protein)',
  'col_evidence' => 'Beleg', 'col_target' => 'Zielprotein', 'col_organism' => 'Organismus', 'col_uniprot' => 'UniProt',
  'col_nr' => 'nr-Eintrag', 'col_len' => 'Länge', 'col_ident' => 'identische Einträge', 'col_orgs' => 'Organismen',
  'col_potency' => 'Wirkstärke (pChEMBL)', 'col_mech' => 'Wirkmechanismus', 'col_blast' => 'Verwandte suchen', 'col_id' => 'ChEMBL-ID',
  'col_compound' => 'Wirkstoff', 'col_phase' => 'Phase', 'col_seq' => 'Sequenz', 'col_desc' => 'Beschreibung',
  'ev_mechanism' => 'Mechanismus', 'ev_activity' => 'Messung', 'not_in_nr' => 'nicht in nr',
  'blast' => 'DIAMOND', 'nr_entry' => 'nr-Eintrag', 'same_seq' => 'identische Sequenz in', 'entries' => 'Einträgen',
  'targets_same' => 'ChEMBL-Ziele mit identischer Sequenz', 'no_target' => 'Kein ChEMBL-Ziel hat genau diese Sequenz. Verwandte Zielproteine findet die DIAMOND-Suche (Link oben); deren Treffer lassen sich hier wieder eingeben.',
  'drugs' => 'Wirkstoffe mit kuratiertem Mechanismus', 'actives' => 'Aktive Verbindungen (beste Wirkstärke zuerst)',
  'organisms' => 'Organismen mit identischer Sequenz', 'col_taxid' => 'Taxon-ID', 'col_rank' => 'Rang', 'col_accs' => 'Accessions',
  'phase4' => 'zugelassen', 'limit_note' => 'ChEMBL führt fast nur Zielproteine von Mensch, Labortieren und Krankheitserregern. Proteine der Giftpflanze oder des Gifttiers selbst (z. B. Enzyme der Alkaloid-Synthese) sind keine Wirkstoffziele und fehlen deshalb.',
  'disclaimer' => 'Forschungsdaten, keine Therapieempfehlung. Identische Sequenz heißt identisches Protein, nicht gleiche Wirkung im Organismus.',
  'source' => 'Quelltext', 'data' => 'Daten: ChEMBL (EMBL-EBI, CC BY-SA 3.0), NCBI nr',
 ],
 'en' => [
  'title' => 'ChEMBL ↔ protein database nr', 'back' => 'ChEMBL query',
  'lead' => 'Links compounds and toxins in ChEMBL with their target proteins in the NCBI protein database nr (1.17 billion sequences) – and back. Every protein links to a search for related proteins (DIAMOND).',
  'mode_compound' => 'Compound/toxin → target proteins in nr', 'mode_target' => 'Target protein → nr', 'mode_protein' => 'nr protein → ChEMBL',
  'ph_compound' => 'e.g. scopolamine, atropine, tetrodotoxin, ziconotide', 'ph_target' => 'e.g. CHRM1, P11229, CHEMBL216',
  'ph_protein' => 'nr accession, e.g. P35498, NP_000729.2, XP_…',
  'help_compound' => 'Name, synonym or ChEMBL ID. Shows the target proteins (curated mechanism first) and their nr entry.',
  'help_target' => 'Gene symbol, UniProt accession, ChEMBL ID or exact target name.',
  'help_protein' => 'Any nr accession. Looks for ChEMBL targets with an identical sequence; related proteins are found by the DIAMOND search.',
  'search' => 'Search', 'human' => 'human targets only', 'organism' => 'Organism (optional)',
  'notfound' => 'Nothing found for', 'didyoumean' => 'Did you mean:', 'error' => 'Query failed', 'ratelimit' => 'Too many requests – please wait a minute.',
  'unavailable' => 'The nr database is not reachable right now.',
  'results' => 'Target proteins', 'excel' => 'Excel file', 'none' => 'No results.',
  'own_seq' => 'Own sequence of the compound (peptide/protein)',
  'col_evidence' => 'Evidence', 'col_target' => 'Target', 'col_organism' => 'Organism', 'col_uniprot' => 'UniProt',
  'col_nr' => 'nr entry', 'col_len' => 'Length', 'col_ident' => 'identical entries', 'col_orgs' => 'Organisms',
  'col_potency' => 'Potency (pChEMBL)', 'col_mech' => 'Mechanism', 'col_blast' => 'Find related', 'col_id' => 'ChEMBL ID',
  'col_compound' => 'Compound', 'col_phase' => 'Phase', 'col_seq' => 'Sequence', 'col_desc' => 'Description',
  'ev_mechanism' => 'mechanism', 'ev_activity' => 'measured', 'not_in_nr' => 'not in nr',
  'blast' => 'DIAMOND', 'nr_entry' => 'nr entry', 'same_seq' => 'identical sequence in', 'entries' => 'entries',
  'targets_same' => 'ChEMBL targets with an identical sequence', 'no_target' => 'No ChEMBL target has exactly this sequence. Related targets are found by the DIAMOND search (link above); its hits can be entered here again.',
  'drugs' => 'Drugs with a curated mechanism', 'actives' => 'Active compounds (best potency first)',
  'organisms' => 'Organisms with an identical sequence', 'col_taxid' => 'Taxon ID', 'col_rank' => 'Rank', 'col_accs' => 'Accessions',
  'phase4' => 'approved', 'limit_note' => 'ChEMBL lists almost only targets of humans, laboratory animals and pathogens. Proteins of the toxic plant or animal itself (e.g. enzymes of alkaloid biosynthesis) are not drug targets and are therefore missing.',
  'disclaimer' => 'Research data, not a treatment recommendation. Identical sequence means identical protein, not the same effect in the organism.',
  'source' => 'Source code', 'data' => 'Data: ChEMBL (EMBL-EBI, CC BY-SA 3.0), NCBI nr',
 ],
][$lang];

function h($s): string { return htmlspecialchars((string)($s ?? ''), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }

function api(string $path, array $params = []): array {
    global $API;
    $ch = curl_init($API . $path . ($params ? '?' . http_build_query($params) : ''));
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 70, CURLOPT_CONNECTTIMEOUT => 5, CURLOPT_ENCODING => '',
        CURLOPT_HTTPHEADER => ['X-Forwarded-For: ' . ($_SERVER['REMOTE_ADDR'] ?? ''), 'Accept: application/json'],
    ]);
    $body = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    return [$code, $body === false ? null : json_decode($body, true)];
}

function chembl_link(?string $id, string $kind): string {
    if (!$id) return '';
    return '<a href="https://www.ebi.ac.uk/chembl/explore/' . $kind . '/' . rawurlencode($id) . '" target="_blank" rel="noopener">' . h($id) . '</a>';
}
function ncbi_link(?string $acc): string {
    if (!$acc) return '';
    return '<a href="https://www.ncbi.nlm.nih.gov/protein/' . rawurlencode($acc) . '" target="_blank" rel="noopener">' . h($acc) . '</a>';
}
function blast_link(?string $url, array $T): string {
    return $url ? '<a class="btn btn-outline-primary btn-sm py-0" href="' . h($url) . '" target="_blank" rel="noopener">' . h($T['blast']) . '</a>' : '';
}
function self_url(array $over): string {
    $p = array_merge($_GET, $over);
    return '?' . http_build_query(array_filter($p, fn($v) => $v !== null && $v !== ''));
}
function phase($p, array $T): string {
    if ($p === null || $p === '') return '–';
    return (float)$p >= 4 ? $T['phase4'] : (string)(float)$p;
}
function orgs_link($oid, $n): string {
    return $oid ? '<a href="' . h(self_url(['oid' => $oid])) . '">' . h($n) . '</a>' : h($n);
}

// ------------------------------------------------------------------ input and query
$mode = in_array($_GET['mode'] ?? '', ['compound', 'target', 'protein'], true) ? $_GET['mode'] : 'compound';
$q = mb_substr(trim((string)($_GET['q'] ?? '')), 0, 200);
$human = !empty($_GET['human']);
$organism = mb_substr(trim((string)($_GET['org'] ?? '')), 0, 100);
$oid = isset($_GET['oid']) && ctype_digit((string)$_GET['oid']) ? (string)$_GET['oid'] : '';

$result = null; $error = null; $notfound = null; $orgs = null; $xlsx = null;
if ($oid !== '') {
    [$code, $data] = api("/nr/organisms/$oid");
    if ($code === 200) $orgs = $data['organisms'] ?? []; else $error = $T['error'] . " ($code)";
}
if ($q !== '') {
    $n = rawurlencode($q);
    [$path, $params] = match ($mode) {
        'compound' => ["/nr/compounds/$n", $human ? ['human' => 'true'] : []],
        'target' => ["/nr/targets/$n", $organism !== '' ? ['organism' => $organism] : []],
        'protein' => ["/nr/proteins/$n", []],
    };
    [$code, $data] = api($path, $params);
    if ($code === 200) {
        $result = $data;
        $xlsx = $path . '?' . http_build_query($params + ['format' => 'xlsx']);
    } elseif ($code === 404) {
        $notfound = $data['detail']['candidates'] ?? [];
    } elseif ($code === 429) {
        $error = $T['ratelimit'];
    } elseif ($code === 503) {
        $error = $T['unavailable'];
    } else {
        $error = $T['error'] . " ($code)";
    }
}

header('Cache-Control: public, max-age=300');
header('X-Content-Type-Options: nosniff');
header("Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'");
?><!doctype html>
<html lang="<?= $lang ?>">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= h($T['title']) ?><?= $q !== '' ? ': ' . h($q) : '' ?></title>
<link rel="stylesheet" href="assets/bootstrap.min.css">
<link rel="stylesheet" href="assets/app.css">
</head>
<body>
<nav class="navbar bg-body-tertiary border-bottom mb-4">
  <div class="container">
    <a class="navbar-brand fw-semibold" href="nr.php?lang=<?= $lang ?>"><?= h($T['title']) ?></a>
    <div class="small">
      <a href="./?lang=<?= $lang ?>" class="me-3">← <?= h($T['back']) ?></a>
      <a href="?<?= h(http_build_query(array_merge($_GET, ['lang' => 'de']))) ?>" class="<?= $lang === 'de' ? 'fw-bold' : '' ?>">DE</a> ·
      <a href="?<?= h(http_build_query(array_merge($_GET, ['lang' => 'en']))) ?>" class="<?= $lang === 'en' ? 'fw-bold' : '' ?>">EN</a>
    </div>
  </div>
</nav>

<main class="container mb-5">
  <p class="lead"><?= h($T['lead']) ?></p>

  <form method="get" class="card card-body shadow-sm mb-4" autocomplete="off">
    <input type="hidden" name="lang" value="<?= $lang ?>">
    <div class="btn-group flex-wrap mb-3" role="group">
      <?php foreach (['compound', 'target', 'protein'] as $m): ?>
        <input type="radio" class="btn-check" name="mode" id="mode_<?= $m ?>" value="<?= $m ?>" <?= $mode === $m ? 'checked' : '' ?>>
        <label class="btn btn-outline-primary" for="mode_<?= $m ?>"><?= h($T['mode_' . $m]) ?></label>
      <?php endforeach; ?>
    </div>
    <div class="row g-2 align-items-start">
      <div class="col-md-8">
        <input type="search" class="form-control form-control-lg" name="q" required value="<?= h($q) ?>" placeholder="<?= h($T['ph_' . $mode]) ?>">
        <div class="form-text"><?= h($T['help_' . $mode]) ?></div>
      </div>
      <div class="col-md-4 d-grid"><button class="btn btn-primary btn-lg" type="submit"><?= h($T['search']) ?></button></div>
    </div>
    <div class="row g-3 mt-1">
      <div class="col-md-6">
        <div class="form-check">
          <input class="form-check-input" type="checkbox" name="human" value="1" id="human" <?= $human ? 'checked' : '' ?>>
          <label class="form-check-label" for="human"><?= h($T['human']) ?> (<?= h($T['mode_compound']) ?>)</label>
        </div>
      </div>
      <div class="col-md-4">
        <input class="form-control form-control-sm" name="org" value="<?= h($organism) ?>" placeholder="<?= h($T['organism']) ?> (<?= h($T['mode_target']) ?>)">
      </div>
    </div>
  </form>

  <?php if ($error): ?><div class="alert alert-warning"><?= h($error) ?></div><?php endif; ?>

  <?php if ($notfound !== null): ?>
    <div class="alert alert-info"><strong><?= h($T['notfound']) ?>:</strong> „<?= h($q) ?>“.
      <?php if ($notfound): ?><?= h($T['didyoumean']) ?>
        <?php foreach (array_slice($notfound, 0, 12) as $c):
            $val = $c['pref_name'] ?? $c['target_chembl_id'] ?? $c['chembl_id'] ?? '';
            $lab = $c['pref_name'] ?? (($c['target'] ?? '') . ' – ' . ($c['organism'] ?? '')); ?>
          <a class="badge text-bg-light text-decoration-none" href="<?= h(self_url(['q' => $val, 'oid' => null])) ?>"><?= h($lab ?: $val) ?></a>
        <?php endforeach; ?>
      <?php endif; ?>
    </div>
  <?php endif; ?>

  <?php if ($orgs !== null): ?>
    <h2 class="h5"><?= h($T['organisms']) ?> (nr OID <?= h($oid) ?>)</h2>
    <div class="table-responsive border rounded mb-4"><table class="table table-sm table-striped align-middle mb-0">
      <thead class="table-light"><tr><th><?= h($T['col_organism']) ?></th><th><?= h($T['col_rank']) ?></th><th><?= h($T['col_taxid']) ?></th><th><?= h($T['entries']) ?></th><th><?= h($T['col_accs']) ?></th></tr></thead>
      <tbody><?php foreach ($orgs as $o): ?>
        <tr><td><em><?= h($o['organism'] ?? '?') ?></em></td><td class="small"><?= h($o['rank']) ?></td><td class="small"><?= h($o['taxid']) ?></td>
            <td><?= h($o['entries']) ?></td><td class="small"><?= implode(', ', array_map('ncbi_link', array_slice(explode(',', (string)$o['accessions']), 0, 20))) ?></td></tr>
      <?php endforeach; ?></tbody>
    </table></div>
  <?php endif; ?>

  <?php if ($result !== null && $mode !== 'protein'): $rows = $result['targets'] ?? []; ?>
    <?php if (!empty($result['sequences'])): ?>
      <h2 class="h5"><?= h($T['own_seq']) ?></h2>
      <div class="table-responsive border rounded mb-4"><table class="table table-sm align-middle mb-0">
        <thead class="table-light"><tr><th><?= h($T['col_id']) ?></th><th><?= h($T['col_desc']) ?></th><th><?= h($T['col_len']) ?></th><th><?= h($T['col_seq']) ?></th><th><?= h($T['col_blast']) ?></th></tr></thead>
        <tbody><?php foreach ($result['sequences'] as $s): ?>
          <tr><td class="small"><?= chembl_link($s['chembl_id'], 'compound') ?></td><td class="small"><?= h($s['description']) ?></td><td><?= h($s['length']) ?></td>
              <td class="small font-monospace text-break"><?= h(mb_strimwidth((string)$s['sequence'], 0, 80, '…')) ?></td><td><?= blast_link($s['homolog_search'], $T) ?></td></tr>
        <?php endforeach; ?></tbody>
      </table></div>
    <?php endif; ?>
    <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
      <h2 class="h4 mb-0 me-auto"><?= h($T['results']) ?>: <?= count($rows) ?></h2>
      <?php if ($rows): ?><a class="btn btn-success" href="<?= h($xlsx) ?>"><?= h($T['excel']) ?></a><?php endif; ?>
    </div>
    <?php if (!$rows): ?><div class="alert alert-light border"><?= h($T['none']) ?></div><?php else: ?>
      <div class="table-responsive border rounded"><table class="table table-sm table-hover table-striped align-middle mb-0">
        <thead class="table-light"><tr>
          <?php if ($mode === 'compound'): ?><th><?= h($T['col_evidence']) ?></th><?php endif; ?>
          <th><?= h($T['col_target']) ?></th><th><?= h($T['col_organism']) ?></th>
          <?php if ($mode === 'compound'): ?><th><?= h($T['col_potency']) ?></th><?php endif; ?>
          <th><?= h($T['col_uniprot']) ?></th><th><?= h($T['col_nr']) ?></th><th><?= h($T['col_len']) ?></th>
          <th><?= h($T['col_ident']) ?></th><th><?= h($T['col_orgs']) ?></th><th><?= h($T['col_blast']) ?></th><th><?= h($T['col_id']) ?></th>
        </tr></thead>
        <tbody><?php foreach ($rows as $r): ?>
          <tr>
            <?php if ($mode === 'compound'): ?>
              <td class="small"><?= h($T['ev_' . ($r['evidence'] ?? 'activity')]) ?><?php if (!empty($r['mechanism'])): ?><br><span class="text-body-secondary"><?= h($r['mechanism']) ?></span><?php endif; ?></td>
            <?php endif; ?>
            <td><?= h($r['target']) ?></td><td><em><?= h($r['organism']) ?></em></td>
            <?php if ($mode === 'compound'): ?><td><?= h($r['best_pchembl']) ?></td><?php endif; ?>
            <td class="small"><?= h($r['uniprot']) ?></td>
            <?php if ($r['in_nr']): ?>
              <td class="small"><?= ncbi_link($r['nr_acc']) ?></td><td><?= h($r['length']) ?></td><td><?= h($r['identical_entries']) ?></td>
              <td><?= orgs_link($r['oid'], $r['organisms']) ?></td><td><?= blast_link($r['homolog_search'], $T) ?></td>
            <?php else: ?>
              <td class="small text-body-secondary" colspan="5"><?= h($T['not_in_nr']) ?></td>
            <?php endif; ?>
            <td class="small"><?= chembl_link($r['target_chembl_id'], 'target') ?></td>
          </tr>
        <?php endforeach; ?></tbody>
      </table></div>
    <?php endif; ?>
  <?php endif; ?>

  <?php if ($result !== null && $mode === 'protein'): $e = $result['nr']; ?>
    <div class="card card-body mb-3">
      <div class="small text-body-secondary"><?= h($T['nr_entry']) ?></div>
      <div class="fs-5"><?= ncbi_link($e['nr_acc']) ?> – <?= h($e['nr_title']) ?></div>
      <div class="small"><?= h($e['length']) ?> aa · <?= h($T['same_seq']) ?> <?= orgs_link($e['oid'], $e['identical_entries']) ?> <?= h($T['entries']) ?>
        <?php if ($e['uniprot']): ?> · UniProt <?= h($e['uniprot']) ?><?php endif; ?> · <?= blast_link($e['homolog_search'], $T) ?></div>
    </div>
    <?php if (!$result['targets']): ?>
      <div class="alert alert-light border"><?= h($T['no_target']) ?></div>
    <?php else: ?>
      <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
        <h2 class="h5 mb-0 me-auto"><?= h($T['targets_same']) ?></h2>
        <a class="btn btn-success btn-sm" href="<?= h($xlsx) ?>"><?= h($T['excel']) ?></a>
      </div>
      <ul class="mb-3"><?php foreach ($result['targets'] as $t): ?>
        <li><?= h($t['target']) ?> – <em><?= h($t['organism']) ?></em> (<?= h($t['target_type']) ?>, <?= chembl_link($t['target_chembl_id'], 'target') ?>)</li>
      <?php endforeach; ?></ul>
      <?php if ($result['mechanisms']): ?>
        <h2 class="h5"><?= h($T['drugs']) ?></h2>
        <div class="table-responsive border rounded mb-3"><table class="table table-sm table-striped mb-0">
          <thead class="table-light"><tr><th><?= h($T['col_compound']) ?></th><th><?= h($T['col_phase']) ?></th><th><?= h($T['col_mech']) ?></th><th><?= h($T['col_id']) ?></th></tr></thead>
          <tbody><?php foreach ($result['mechanisms'] as $m): ?>
            <tr><td><?= h($m['compound']) ?></td><td><?= h(phase($m['max_phase'], $T)) ?></td><td class="small"><?= h($m['mechanism_of_action']) ?></td><td class="small"><?= chembl_link($m['compound_chembl_id'], 'compound') ?></td></tr>
          <?php endforeach; ?></tbody>
        </table></div>
      <?php endif; ?>
      <?php if ($result['compounds']): ?>
        <h2 class="h5"><?= h($T['actives']) ?></h2>
        <div class="table-responsive border rounded"><table class="table table-sm table-striped mb-0">
          <thead class="table-light"><tr><th><?= h($T['col_compound']) ?></th><th><?= h($T['col_phase']) ?></th><th><?= h($T['col_potency']) ?></th><th><?= h($T['col_id']) ?></th></tr></thead>
          <tbody><?php foreach ($result['compounds'] as $c): ?>
            <tr><td><?= h($c['compound'] ?: '–') ?></td><td><?= h(phase($c['max_phase'], $T)) ?></td><td><?= h($c['best_pchembl']) ?></td><td class="small"><?= chembl_link($c['compound_chembl_id'], 'compound') ?></td></tr>
          <?php endforeach; ?></tbody>
        </table></div>
      <?php endif; ?>
    <?php endif; ?>
  <?php endif; ?>

  <div class="alert alert-secondary small mt-4"><?= h($T['limit_note']) ?></div>
  <div class="alert alert-secondary small"><?= h($T['disclaimer']) ?></div>
</main>

<footer class="border-top py-3 small text-body-secondary">
  <div class="container d-flex flex-wrap gap-3">
    <a href="./?lang=<?= $lang ?>"><?= h($T['back']) ?></a>
    <a href="https://github.com/gerontec/ChEMBL" target="_blank" rel="noopener"><?= h($T['source']) ?></a>
    <span><?= h($T['data']) ?></span>
  </div>
</footer>
</body>
</html>
