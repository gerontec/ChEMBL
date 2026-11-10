<?php
/*
 * Query form for the ChEMBL query service (github.com/gerontec/ChEMBL), PHP + Bootstrap 5 (served locally,
 * no CDN: no visitor address goes to third parties).
 *
 * The page calls the REST API server side. The API base URL comes from, in this order:
 *   environment CHEMBL_API (Apache: SetEnv, needs mod_env),
 *   /etc/chembl/web.ini with a line  api = "http://<api-host>:9099",
 *   http://127.0.0.1:9099.
 * The visitor address is passed on as X-Forwarded-For so the API's per-client rate limit applies per visitor.
 */

declare(strict_types=1);

const PAGE_ROWS = 1000;        // rows per page on screen
const XLSX_ROWS = 10000;       // rows in the Excel download (API maximum per request)

$ini = is_readable('/etc/chembl/web.ini') ? (parse_ini_file('/etc/chembl/web.ini') ?: []) : [];
$API = rtrim(getenv('CHEMBL_API') ?: ($ini['api'] ?? 'http://127.0.0.1:9099'), '/');

// ------------------------------------------------------------------ language
$lang = (($_GET['lang'] ?? '') === 'en' || (!isset($_GET['lang'])
        && !str_starts_with(strtolower($_SERVER['HTTP_ACCEPT_LANGUAGE'] ?? 'de'), 'de'))) ? 'en' : 'de';

$T = [
 'de' => [
  'title' => 'ChEMBL-Abfrage', 'lead' => 'Wirkstoffe, Zielstrukturen und klinische Studien aus der Datenbank ChEMBL – ohne Programmierkenntnisse.',
  'mode_compound' => 'Wirkstoff → Zielstrukturen', 'mode_target' => 'Zielstruktur → Wirkstoffe', 'mode_disease' => 'Krankheit → Wirkstoffe in Studien',
  'ph_compound' => 'z. B. Ivermectin, Hydroxychloroquin, Nirmatrelvir', 'ph_target' => 'z. B. ABCB1, P-Glycoprotein, CHEMBL240, P08183',
  'ph_disease' => 'z. B. COVID-19, Malaria, Alzheimer',
  'help_compound' => 'Name, Handelsname oder ChEMBL-ID. Salze und andere Formen werden mitgesucht.',
  'help_target' => 'Gensymbol, Proteinname, UniProt-Nummer oder ChEMBL-ID. Bei Gensymbolen ggf. Organismus wählen.',
  'help_disease' => 'Englischer Krankheitsname (Anfang genügt).',
  'levels' => 'Evidenzstufen', 'human' => 'nur menschliche Zielstrukturen', 'organism' => 'Organismus',
  'org_any' => 'alle', 'approved' => 'nur zugelassene Arzneimittel', 'named' => 'nur Wirkstoffe mit Namen',
  'search' => 'Abfragen', 'excel' => 'Als Excel herunterladen', 'results' => 'Ergebnisse', 'of' => 'von',
  'shown' => 'angezeigt', 'more' => 'Nächste Seite', 'prev' => 'Vorherige Seite', 'notfound' => 'Nicht gefunden',
  'didyoumean' => 'Meinten Sie:', 'none' => 'Keine Einträge mit diesen Filtern.', 'toomany' => 'Mehr als 10 000 Zeilen: für die vollständige Liste unten einen großen Auftrag starten.',
  'ratelimit' => 'Zu viele Abfragen in kurzer Zeit. Bitte eine Minute warten.', 'error' => 'Fehler bei der Abfrage',
  'compound' => 'Wirkstoff', 'target' => 'Zielstruktur', 'targets_found' => 'Gefundene Zielstruktur(en)',
  'col_evidence' => 'Evidenz', 'col_target' => 'Zielstruktur', 'col_organism' => 'Organismus', 'col_type' => 'Typ',
  'col_compound' => 'Wirkstoff', 'col_phase' => 'Zulassung', 'col_potency' => 'Potenz (pChEMBL)', 'col_conc' => '≈ Konzentration',
  'col_meas' => 'Messungen', 'col_docs' => 'Publikationen', 'col_mech' => 'Kuratierter Mechanismus', 'col_id' => 'ChEMBL-ID',
  'col_indphase' => 'Studienphase', 'col_indication' => 'Indikation', 'col_refs' => 'Quellen',
  'phase4' => 'zugelassen', 'phase' => 'Phase', 'phase_early' => 'frühe Phase 1', 'phase_unknown' => 'unbekannt',
  'legend' => 'Was bedeuten die Spalten?',
  'leg_A' => 'Kuratierter Wirkmechanismus (von ChEMBL-Fachleuten eingetragen, meist zugelassene Arzneimittel)',
  'leg_B' => 'Gemessen am Molekül, hohe Messqualität, ≤ 100 nM, in mindestens 2 Publikationen bestätigt',
  'leg_C' => 'Gemessen am Molekül, hohe Messqualität, ≤ 1 µM', 'leg_D' => 'Gemessen am Molekül, ≤ 10 µM',
  'leg_P' => 'Gemessen am ganzen Erreger, an Zellen oder Gewebe (ohne bekanntes Zielmolekül), ≤ 10 µM',
  'leg_pchembl' => 'Potenz: je höher, desto weniger Substanz ist nötig. 6 ≈ 1 µM, 9 ≈ 1 nM. Daneben die Konzentration.',
  'leg_phase' => 'Zulassung: höchste erreichte klinische Phase für irgendeine Indikation (4 = zugelassen).',
  'disclaimer' => 'Die Werte stammen aus Laborexperimenten (Enzymtests, Zellkultur) der öffentlichen Datenbank ChEMBL. Sie sagen nichts über klinische Wirksamkeit, Dosierung oder Sicherheit beim Menschen aus und sind keine Therapieempfehlung.',
  'indication_note' => 'Aufgeführt ist jeder Wirkstoff, der für diese Indikation eine klinische Studie erreicht hat – unabhängig vom Ergebnis der Studie.',
  'batch' => 'Großer Auftrag (viele Namen, sehr lange Listen)', 'batch_help' => 'Ein Name pro Zeile, bis 5 000. Es gilt die oben gewählte Frage mit ihren Filtern. Das Ergebnis kommt als Excel-Datei.',
  'batch_names' => 'Namen', 'email' => 'E-Mail (optional, für den Download-Link)', 'batch_start' => 'Auftrag starten',
  'batch_queued' => 'In der Warteschlange', 'batch_running' => 'Läuft', 'batch_done' => 'Fertig', 'batch_failed' => 'Fehlgeschlagen',
  'batch_download' => 'Excel-Datei herunterladen', 'batch_keep' => 'Bitte diese Seite offen lassen oder den Link speichern:',
  'rows' => 'Zeilen', 'names' => 'Namen', 'experts' => 'Für Fachleute: REST-API mit Dokumentation', 'source' => 'Quellcode',
  'data' => 'Daten: ChEMBL (EMBL-EBI), CC BY-SA 3.0',
 ],
 'en' => [
  'title' => 'ChEMBL query', 'lead' => 'Compounds, targets and clinical trials from the ChEMBL database – no programming needed.',
  'mode_compound' => 'Compound → targets', 'mode_target' => 'Target → compounds', 'mode_disease' => 'Disease → compounds in trials',
  'ph_compound' => 'e.g. ivermectin, hydroxychloroquine, nirmatrelvir', 'ph_target' => 'e.g. ABCB1, P-glycoprotein, CHEMBL240, P08183',
  'ph_disease' => 'e.g. COVID-19, malaria, Alzheimer',
  'help_compound' => 'Name, trade name or ChEMBL ID. Salts and other forms are included.',
  'help_target' => 'Gene symbol, protein name, UniProt accession or ChEMBL ID. Pick an organism for gene symbols if needed.',
  'help_disease' => 'Disease name (the beginning is enough).',
  'levels' => 'Evidence levels', 'human' => 'human targets only', 'organism' => 'Organism',
  'org_any' => 'any', 'approved' => 'approved drugs only', 'named' => 'named compounds only',
  'search' => 'Search', 'excel' => 'Download as Excel', 'results' => 'Results', 'of' => 'of',
  'shown' => 'shown', 'more' => 'Next page', 'prev' => 'Previous page', 'notfound' => 'Not found',
  'didyoumean' => 'Did you mean:', 'none' => 'No entries with these filters.', 'toomany' => 'More than 10 000 rows: start a large job below for the complete list.',
  'ratelimit' => 'Too many requests in a short time. Please wait a minute.', 'error' => 'Query error',
  'compound' => 'Compound', 'target' => 'Target', 'targets_found' => 'Target(s) found',
  'col_evidence' => 'Evidence', 'col_target' => 'Target', 'col_organism' => 'Organism', 'col_type' => 'Type',
  'col_compound' => 'Compound', 'col_phase' => 'Approval', 'col_potency' => 'Potency (pChEMBL)', 'col_conc' => '≈ concentration',
  'col_meas' => 'Measurements', 'col_docs' => 'Publications', 'col_mech' => 'Curated mechanism', 'col_id' => 'ChEMBL ID',
  'col_indphase' => 'Trial phase', 'col_indication' => 'Indication', 'col_refs' => 'Sources',
  'phase4' => 'approved', 'phase' => 'Phase', 'phase_early' => 'early phase 1', 'phase_unknown' => 'unknown',
  'legend' => 'What do the columns mean?',
  'leg_A' => 'Curated mechanism of action (entered by ChEMBL curators, mostly approved drugs)',
  'leg_B' => 'Measured on the molecule, high assay quality, ≤ 100 nM, confirmed in at least 2 publications',
  'leg_C' => 'Measured on the molecule, high assay quality, ≤ 1 µM', 'leg_D' => 'Measured on the molecule, ≤ 10 µM',
  'leg_P' => 'Measured on the whole pathogen, cells or tissue (no known target molecule), ≤ 10 µM',
  'leg_pchembl' => 'Potency: the higher, the less substance is needed. 6 ≈ 1 µM, 9 ≈ 1 nM. The concentration is shown next to it.',
  'leg_phase' => 'Approval: highest clinical phase reached for any indication (4 = approved).',
  'disclaimer' => 'The values come from laboratory experiments (enzyme assays, cell culture) in the public ChEMBL database. They say nothing about clinical efficacy, dose or safety in humans and are no treatment recommendation.',
  'indication_note' => 'Every compound that reached a clinical trial for this indication is listed, whatever the outcome of the trial.',
  'batch' => 'Large job (many names, very long lists)', 'batch_help' => 'One name per line, up to 5 000. The question and filters chosen above apply. The result is an Excel file.',
  'batch_names' => 'Names', 'email' => 'E-mail (optional, for the download link)', 'batch_start' => 'Start job',
  'batch_queued' => 'Queued', 'batch_running' => 'Running', 'batch_done' => 'Done', 'batch_failed' => 'Failed',
  'batch_download' => 'Download Excel file', 'batch_keep' => 'Keep this page open or save the link:',
  'rows' => 'rows', 'names' => 'names', 'experts' => 'For experts: REST API with documentation', 'source' => 'Source code',
  'data' => 'Data: ChEMBL (EMBL-EBI), CC BY-SA 3.0',
 ],
][$lang];

function h($s): string { return htmlspecialchars((string)($s ?? ''), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }

// ------------------------------------------------------------------ input
$mode = in_array($_GET['mode'] ?? '', ['compound', 'target', 'disease'], true) ? $_GET['mode'] : 'compound';
$q = trim((string)($_GET['q'] ?? ''));
$q = mb_substr($q, 0, 200);
$allLevels = ['A', 'B', 'C', 'D', 'P'];
$levels = array_values(array_intersect($allLevels, (array)($_GET['lv'] ?? [])));
if (!$levels) { $levels = $mode === 'target' ? ['A', 'B', 'C', 'D'] : $allLevels; }
$human = !empty($_GET['human']);
$approved = !empty($_GET['approved']);
$named = !empty($_GET['named']);
$organism = trim((string)($_GET['org'] ?? ''));
$page = max(1, (int)($_GET['page'] ?? 1));

// ------------------------------------------------------------------ API call
function api(string $path, array $params = []): array {
    global $API;
    $url = $API . $path . ($params ? '?' . http_build_query($params) : '');
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 70, CURLOPT_CONNECTTIMEOUT => 5, CURLOPT_ENCODING => '',
        CURLOPT_HTTPHEADER => ['X-Forwarded-For: ' . ($_SERVER['REMOTE_ADDR'] ?? ''), 'Accept: application/json'],
    ]);
    $body = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    return [$code, $body === false ? null : json_decode($body, true)];
}

/** endpoint path and query parameters for the chosen question (shared by screen, Excel and batch job) */
function query_spec(string $mode, string $q, array $levels, bool $human, bool $approved, bool $named, string $organism): array {
    $n = rawurlencode($q);
    if ($mode === 'compound') {
        $p = ['levels' => implode('', $levels)];
        if ($human) $p['human'] = 'true';
        return ["/compounds/$n/candidates", '/compounds/{name}/candidates', $p];
    }
    if ($mode === 'target') {
        $p = ['levels' => implode('', $levels)];
        if ($organism !== '') $p['organism'] = $organism;
        if ($approved) $p['min_phase'] = 4;
        if ($named) $p['named'] = 'true';
        return ["/targets/$n/candidates", '/targets/{name}/candidates', $p];
    }
    $p = $approved ? ['min_phase' => 4] : [];
    return ["/indications/$n", '/indications/{name}', $p];
}

function phase_label($p, array $T): string {
    if ($p === null || $p === '') return '–';
    $p = (float)$p;
    if ($p >= 4) return $T['phase4'];
    if ($p == 0.5) return $T['phase_early'];
    if ($p < 0) return $T['phase_unknown'];
    return $T['phase'] . ' ' . (int)$p;
}

function conc($p): string {
    if ($p === null || $p === '') return '';
    $nm = pow(10, 9 - (float)$p);
    if ($nm < 1) return '≈ ' . round($nm * 1000, $nm * 1000 < 10 ? 1 : 0) . ' pM';
    if ($nm < 1000) return '≈ ' . round($nm, $nm < 10 ? 1 : 0) . ' nM';
    if ($nm < 1e6) return '≈ ' . round($nm / 1000, $nm < 1e4 ? 1 : 0) . ' µM';
    return '≈ ' . round($nm / 1e6, 1) . ' mM';
}

function badge(string $e): string {
    $c = ['A' => 'text-bg-success', 'B' => 'text-bg-primary', 'C' => 'text-bg-info', 'D' => 'text-bg-secondary',
          'P' => 'text-bg-warning'][$e] ?? 'text-bg-light';
    return '<span class="badge ' . $c . '">' . h($e) . '</span>';
}

function chembl_link(?string $id, string $kind): string {
    if (!$id) return '';
    return '<a href="https://www.ebi.ac.uk/chembl/explore/' . $kind . '/' . rawurlencode($id) . '" target="_blank" rel="noopener">' . h($id) . '</a>';
}

function self_url(array $over): string {
    $p = array_merge($_GET, $over);
    return '?' . http_build_query(array_filter($p, fn($v) => $v !== null && $v !== '' && $v !== []));
}

// ------------------------------------------------------------------ run query
$result = null; $error = null; $notfound = null; $compoundInfo = null;
[$path, $jobEndpoint, $params] = query_spec($mode, $q, $levels, $human, $approved, $named, $organism);
if ($q !== '') {
    $screen = $params;
    if ($mode === 'target') { $screen['limit'] = PAGE_ROWS; $screen['offset'] = ($page - 1) * PAGE_ROWS; }
    [$code, $data] = api($path, $screen);
    if ($code === 200) {
        $result = $data;
        if ($mode === 'compound') { [$c2, $compoundInfo] = api('/compounds/' . rawurlencode($q)); }
    } elseif ($code === 404) {
        $notfound = $data['detail']['candidates'] ?? [];
    } elseif ($code === 429) {
        $error = $T['ratelimit'];
    } else {
        $error = $T['error'] . " ($code)";
    }
}
$rows = $result ? ($result['candidates'] ?? $result['compounds'] ?? []) : [];
$total = $result['total'] ?? count($rows);
$xlsxParams = $params + ['format' => 'xlsx'] + ($mode === 'target' ? ['limit' => XLSX_ROWS] : []);
$xlsxUrl = $path . '?' . http_build_query($xlsxParams);

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
    <a class="navbar-brand fw-semibold" href="?lang=<?= $lang ?>"><?= h($T['title']) ?></a>
    <div class="small">
      <a href="nr.php?lang=<?= $lang ?>" class="me-3">ChEMBL ↔ nr</a>
      <a href="?<?= h(http_build_query(array_merge($_GET, ['lang' => 'de']))) ?>" class="<?= $lang === 'de' ? 'fw-bold' : '' ?>">DE</a> ·
      <a href="?<?= h(http_build_query(array_merge($_GET, ['lang' => 'en']))) ?>" class="<?= $lang === 'en' ? 'fw-bold' : '' ?>">EN</a>
    </div>
  </div>
</nav>

<main class="container mb-5">
  <p class="lead"><?= h($T['lead']) ?></p>

  <form method="get" id="qform" class="card card-body shadow-sm mb-4" autocomplete="off">
    <input type="hidden" name="lang" value="<?= $lang ?>">
    <div class="btn-group flex-wrap mb-3" role="group">
      <?php foreach (['compound', 'target', 'disease'] as $m): ?>
        <input type="radio" class="btn-check" name="mode" id="mode_<?= $m ?>" value="<?= $m ?>" <?= $mode === $m ? 'checked' : '' ?>>
        <label class="btn btn-outline-primary" for="mode_<?= $m ?>"><?= h($T['mode_' . $m]) ?></label>
      <?php endforeach; ?>
    </div>

    <div class="row g-2 align-items-start">
      <div class="col-md-8">
        <input type="search" class="form-control form-control-lg" name="q" id="q" list="sugg" required
               value="<?= h($q) ?>" placeholder="<?= h($T['ph_' . $mode]) ?>"
               data-ph-compound="<?= h($T['ph_compound']) ?>" data-ph-target="<?= h($T['ph_target']) ?>" data-ph-disease="<?= h($T['ph_disease']) ?>">
        <datalist id="sugg"></datalist>
        <div class="form-text" id="qhelp" data-help-compound="<?= h($T['help_compound']) ?>"
             data-help-target="<?= h($T['help_target']) ?>" data-help-disease="<?= h($T['help_disease']) ?>"><?= h($T['help_' . $mode]) ?></div>
      </div>
      <div class="col-md-4 d-grid">
        <button class="btn btn-primary btn-lg" type="submit"><?= h($T['search']) ?></button>
      </div>
    </div>

    <div class="row g-3 mt-1">
      <div class="col-md-6 opt opt-compound opt-target">
        <div class="small text-body-secondary mb-1"><?= h($T['levels']) ?></div>
        <?php foreach ($allLevels as $l): ?>
          <div class="form-check form-check-inline" title="<?= h($T['leg_' . $l]) ?>">
            <input class="form-check-input" type="checkbox" name="lv[]" value="<?= $l ?>" id="lv<?= $l ?>" <?= in_array($l, $levels, true) ? 'checked' : '' ?>>
            <label class="form-check-label" for="lv<?= $l ?>"><?= badge($l) ?></label>
          </div>
        <?php endforeach; ?>
      </div>
      <div class="col-md-6 opt opt-compound">
        <div class="form-check mt-md-4">
          <input class="form-check-input" type="checkbox" name="human" value="1" id="human" <?= $human ? 'checked' : '' ?>>
          <label class="form-check-label" for="human"><?= h($T['human']) ?></label>
        </div>
      </div>
      <div class="col-md-3 opt opt-target">
        <label class="small text-body-secondary mb-1" for="org"><?= h($T['organism']) ?></label>
        <input class="form-control form-control-sm" name="org" id="org" list="orgs" value="<?= h($organism) ?>" placeholder="<?= h($T['org_any']) ?>">
        <datalist id="orgs">
          <option value="Homo sapiens"><option value="Severe acute respiratory syndrome coronavirus 2">
          <option value="Plasmodium falciparum"><option value="Mus musculus"><option value="Rattus norvegicus">
        </datalist>
      </div>
      <div class="col-md-3 opt opt-target opt-disease">
        <div class="form-check mt-md-4">
          <input class="form-check-input" type="checkbox" name="approved" value="1" id="approved" <?= $approved ? 'checked' : '' ?>>
          <label class="form-check-label" for="approved"><?= h($T['approved']) ?></label>
        </div>
        <div class="form-check opt opt-target">
          <input class="form-check-input" type="checkbox" name="named" value="1" id="named" <?= $named ? 'checked' : '' ?>>
          <label class="form-check-label" for="named"><?= h($T['named']) ?></label>
        </div>
      </div>
    </div>
  </form>

  <?php if ($error): ?>
    <div class="alert alert-warning"><?= h($error) ?></div>
  <?php endif; ?>

  <?php if ($notfound !== null): ?>
    <div class="alert alert-info">
      <strong><?= h($T['notfound']) ?>:</strong> „<?= h($q) ?>“.
      <?php if ($notfound): ?>
        <?= h($T['didyoumean']) ?>
        <?php foreach (array_slice($notfound, 0, 12) as $c):
            $val = $c['pref_name'] ?? $c['target_chembl_id'] ?? $c['chembl_id'] ?? '';
            $lab = $c['pref_name'] ?? (($c['target'] ?? '') . ' – ' . ($c['organism'] ?? '')); ?>
          <a class="badge text-bg-light text-decoration-none" href="<?= h(self_url(['q' => $val ?: ($c['chembl_id'] ?? ''), 'page' => null])) ?>"><?= h($lab ?: $c['chembl_id']) ?></a>
        <?php endforeach; ?>
      <?php endif; ?>
    </div>
  <?php endif; ?>

  <?php if ($result !== null): ?>
    <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
      <h2 class="h4 mb-0 me-auto">
        <?= h($T['results']) ?>: <?= number_format((int)$total, 0, ',', '.') ?>
        <?php if ($total > count($rows)): ?><small class="text-body-secondary">(<?= count($rows) ?> <?= h($T['shown']) ?>)</small><?php endif; ?>
      </h2>
      <?php if ($rows): ?>
        <a class="btn btn-success" href="<?= h($xlsxUrl) ?>"><?= h($T['excel']) ?></a>
      <?php endif; ?>
    </div>

    <?php if ($mode === 'compound' && !empty($compoundInfo['forms'])): ?>
      <p class="text-body-secondary small mb-2"><?= h($T['compound']) ?>:
        <?php foreach ($compoundInfo['forms'] as $f): ?>
          <span class="me-2"><?= h($f['pref_name'] ?: $f['chembl_id']) ?> (<?= chembl_link($f['chembl_id'], 'compound') ?>, <?= h(phase_label($f['max_phase'], $T)) ?>)</span>
        <?php endforeach; ?>
      </p>
    <?php elseif ($mode === 'target' && !empty($result['targets'])): ?>
      <p class="text-body-secondary small mb-2"><?= h($T['targets_found']) ?>:
        <?php foreach ($result['targets'] as $t): ?>
          <span class="me-2"><?= h($t['target']) ?> – <em><?= h($t['organism']) ?></em> (<?= chembl_link($t['target_chembl_id'], 'target') ?>)</span>
        <?php endforeach; ?>
      </p>
    <?php elseif ($mode === 'disease'): ?>
      <p class="text-body-secondary small mb-2"><?= h($T['indication_note']) ?></p>
    <?php endif; ?>

    <?php if ($total > XLSX_ROWS): ?><div class="alert alert-info py-2"><?= h($T['toomany']) ?></div><?php endif; ?>

    <?php if (!$rows): ?>
      <div class="alert alert-light border"><?= h($T['none']) ?></div>
    <?php else: ?>
      <div class="table-responsive border rounded">
      <table class="table table-sm table-hover table-striped align-middle mb-0 sortable">
        <thead class="table-light"><tr>
        <?php if ($mode === 'compound'): ?>
          <th><?= h($T['col_evidence']) ?></th><th><?= h($T['col_target']) ?></th><th><?= h($T['col_organism']) ?></th>
          <th data-num><?= h($T['col_potency']) ?></th><th><?= h($T['col_conc']) ?></th><th data-num><?= h($T['col_meas']) ?></th>
          <th data-num><?= h($T['col_docs']) ?></th><th><?= h($T['col_mech']) ?></th><th><?= h($T['col_id']) ?></th>
        <?php elseif ($mode === 'target'): ?>
          <th><?= h($T['col_evidence']) ?></th><th><?= h($T['col_compound']) ?></th><th data-num><?= h($T['col_phase']) ?></th>
          <th data-num><?= h($T['col_potency']) ?></th><th><?= h($T['col_conc']) ?></th><th data-num><?= h($T['col_meas']) ?></th>
          <th data-num><?= h($T['col_docs']) ?></th><th><?= h($T['col_mech']) ?></th><th><?= h($T['col_id']) ?></th>
        <?php else: ?>
          <th><?= h($T['col_compound']) ?></th><th data-num><?= h($T['col_indphase']) ?></th><th data-num><?= h($T['col_phase']) ?></th>
          <th><?= h($T['col_indication']) ?></th><th><?= h($T['col_refs']) ?></th><th><?= h($T['col_id']) ?></th>
        <?php endif; ?>
        </tr></thead>
        <tbody>
        <?php foreach ($rows as $r): ?>
          <tr>
          <?php if ($mode === 'compound'): ?>
            <td><?= badge($r['evidence'] ?? '-') ?></td><td><?= h($r['target']) ?></td><td><em><?= h($r['organism']) ?></em></td>
            <td data-v="<?= h($r['best_pchembl']) ?>"><?= h($r['best_pchembl']) ?></td><td class="text-nowrap"><?= h(conc($r['best_pchembl'])) ?></td>
            <td data-v="<?= h($r['n_meas']) ?>"><?= h($r['n_meas']) ?></td><td data-v="<?= h($r['n_docs_active']) ?>"><?= h($r['n_docs_active']) ?></td>
            <td class="small"><?= h($r['mechanism_of_action']) ?></td><td class="small"><?= chembl_link($r['target_chembl_id'], 'target') ?></td>
          <?php elseif ($mode === 'target'): ?>
            <td><?= badge($r['evidence'] ?? '-') ?></td><td><?= h($r['compound'] ?: '–') ?></td>
            <td data-v="<?= h($r['max_phase']) ?>"><?= h(phase_label($r['max_phase'], $T)) ?></td>
            <td data-v="<?= h($r['best_pchembl']) ?>"><?= h($r['best_pchembl']) ?></td><td class="text-nowrap"><?= h(conc($r['best_pchembl'])) ?></td>
            <td data-v="<?= h($r['n_meas']) ?>"><?= h($r['n_meas']) ?></td><td data-v="<?= h($r['n_docs_active']) ?>"><?= h($r['n_docs_active']) ?></td>
            <td class="small"><?= h($r['mechanism_of_action']) ?></td><td class="small"><?= chembl_link($r['compound_chembl_id'], 'compound') ?></td>
          <?php else:
              $refs = array_values(array_filter(explode(' ', (string)($r['ref_urls'] ?? '')))); ?>
            <td><?= h($r['compound'] ?: '–') ?></td>
            <td data-v="<?= h($r['phase_for_indication']) ?>"><?= h(phase_label($r['phase_for_indication'], $T)) ?></td>
            <td data-v="<?= h($r['max_phase_any']) ?>"><?= h(phase_label($r['max_phase_any'], $T)) ?></td>
            <td class="small"><?= h($r['efo_term'] ?: $r['mesh_heading']) ?></td>
            <td class="small"><?php if ($refs): ?><a href="<?= h($refs[0]) ?>" target="_blank" rel="noopener"><?= h($r['ref_types']) ?></a><?php else: ?><?= h($r['ref_types']) ?><?php endif; ?></td>
            <td class="small"><?= chembl_link($r['compound_chembl_id'], 'compound') ?></td>
          <?php endif; ?>
          </tr>
        <?php endforeach; ?>
        </tbody>
      </table>
      </div>
      <?php if ($mode === 'target' && $total > PAGE_ROWS): ?>
        <nav class="mt-2 d-flex gap-2">
          <?php if ($page > 1): ?><a class="btn btn-outline-secondary btn-sm" href="<?= h(self_url(['page' => $page - 1])) ?>">← <?= h($T['prev']) ?></a><?php endif; ?>
          <?php if ($page * PAGE_ROWS < $total): ?><a class="btn btn-outline-secondary btn-sm" href="<?= h(self_url(['page' => $page + 1])) ?>"><?= h($T['more']) ?> →</a><?php endif; ?>
        </nav>
      <?php endif; ?>
    <?php endif; ?>
  <?php endif; ?>

  <div class="alert alert-secondary small mt-4"><?= h($T['disclaimer']) ?></div>

  <details class="card card-body mb-3">
    <summary class="fw-semibold"><?= h($T['legend']) ?></summary>
    <dl class="row small mt-2 mb-0">
      <?php foreach ($allLevels as $l): ?>
        <dt class="col-2 col-md-1"><?= badge($l) ?></dt><dd class="col-10 col-md-11"><?= h($T['leg_' . $l]) ?></dd>
      <?php endforeach; ?>
      <dt class="col-2 col-md-1">pChEMBL</dt><dd class="col-10 col-md-11"><?= h($T['leg_pchembl']) ?></dd>
      <dt class="col-2 col-md-1"><?= h($T['col_phase']) ?></dt><dd class="col-10 col-md-11"><?= h($T['leg_phase']) ?></dd>
    </dl>
  </details>

  <details class="card card-body mb-3" id="batchbox">
    <summary class="fw-semibold"><?= h($T['batch']) ?></summary>
    <p class="small text-body-secondary mt-2"><?= h($T['batch_help']) ?></p>
    <form id="batchform" data-endpoint-compound="/compounds/{name}/candidates" data-endpoint-target="/targets/{name}/candidates"
          data-endpoint-disease="/indications/{name}"
          data-t-queued="<?= h($T['batch_queued']) ?>" data-t-running="<?= h($T['batch_running']) ?>" data-t-done="<?= h($T['batch_done']) ?>"
          data-t-failed="<?= h($T['batch_failed']) ?>" data-t-download="<?= h($T['batch_download']) ?>" data-t-keep="<?= h($T['batch_keep']) ?>"
          data-t-rows="<?= h($T['rows']) ?>" data-t-names="<?= h($T['names']) ?>">
      <label class="form-label small" for="bnames"><?= h($T['batch_names']) ?></label>
      <textarea class="form-control mb-2" id="bnames" rows="6" required></textarea>
      <div class="row g-2">
        <div class="col-md-8"><input type="email" class="form-control" id="bmail" placeholder="<?= h($T['email']) ?>"></div>
        <div class="col-md-4 d-grid"><button class="btn btn-outline-primary" type="submit"><?= h($T['batch_start']) ?></button></div>
      </div>
    </form>
    <div id="batchstatus" class="mt-3"></div>
  </details>
</main>

<footer class="border-top py-3 small text-body-secondary">
  <div class="container d-flex flex-wrap gap-3">
    <a href="docs"><?= h($T['experts']) ?></a>
    <a href="https://github.com/gerontec/ChEMBL" target="_blank" rel="noopener"><?= h($T['source']) ?></a>
    <span><?= h($T['data']) ?></span>
  </div>
</footer>
<script src="assets/app.js"></script>
</body>
</html>
