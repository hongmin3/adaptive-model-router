#!/usr/bin/env node
'use strict';
// SPEC.md를 사람이 읽는 단일 HTML(docs/SPEC.html)로 만든다. 원본은 언제나 SPEC.md이고,
// HTML은 그 사본이다 — 직접 고치지 않는다.
//
// 이 파일은 키트의 tools/와 각 프로젝트의 .project-check/에 같은 바이트로 놓인다
// (migrate-project-readiness.js가 관리 사본으로 배포한다). 그래서 Node 내장 모듈만 쓴다.
// project-readiness.js는 같은 디렉터리의 이 모듈로 신선도를 판정하므로 해시 규칙은 한 벌이다.
//
// 성질: 외부 리소스 0개(오프라인·비공개로 열린다), 결정적 출력(생성 시각 없음 — 같은 SPEC이면
// 같은 바이트라 커밋에 소음이 없다), SPEC 속 원문 HTML은 escape되어 실행되지 않는다.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

// v2: 기능 목록·카드 메타(상태·구현·테스트·역참조)·요구사항별 변경 이력·로컬 이미지.
// v3: ```flow 흐름도(inline SVG), 이력이 없는 요구사항의 "기록 없음" 표시.
// v4: 지금 읽는 절·요구사항을 왼쪽 목차에 표시(scroll spy, aria-current).
// v5: 카드 소제목(####)을 이름표-내용 칸으로, 번호 목록을 단계로, `> **예외**` 인용을 색 상자로,
//     `이유:` 줄을 따로 보이고, `| 용어 | 뜻 |` 표의 용어에 마우스를 올리면 뜻을 보인다.
// v6: fence·표 파서를 준비 검사와 한 벌로(탭 들여쓰기, 4칸 들여쓴 닫는 표시는 닫지 않음, code span 속 `|`).
const RENDERER_VERSION = 'v6';
const OUTPUT = 'docs/SPEC.html';
const REGENERATE = 'node .project-check/render-spec-html.js .';
const ID = /\b(?:REQ|NFR|TEST)-[A-Z0-9]+-\d{3}\b/g;
const STATUSES = ['draft', 'implemented', 'verified', 'deprecated'];
const BADGES = new Set([...STATUSES, 'active']);
const KIND = { REQ: ['req', '기능 요구사항 REQ'], NFR: ['nfr', '비기능 NFR'], TEST: ['test', '테스트 TEST'] };

const normalize = text => text.replace(/^﻿/, '').replace(/\r\n?/g, '\n');
// BOM·CRLF를 정규화한 뒤 해시한다. Windows에서 core.autocrlf로 checkout된 SPEC이 같은 해시를
// 내야 HTML이 낡은 것으로 오판되지 않는다.
// 요구사항별 변경 이력을 CHANGELOG.md에서 가져오므로 두 원본을 함께 해시한다. CHANGELOG가 없으면
// SPEC만 해시한다.
function sourceHash(text, changelog = '') {
  const h = crypto.createHash('sha256').update(normalize(text), 'utf8');
  if (changelog) h.update('\n\u0000CHANGELOG\u0000\n' + normalize(changelog), 'utf8');
  return h.digest('hex');
}
function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

// ---- inline -------------------------------------------------------------------------------

function safeHref(href) {
  if (href.startsWith('#')) return href;
  if (/^(?:https?:|mailto:)/i.test(href)) return href;
  // 다른 scheme(javascript: 등), protocol-relative, 절대 경로(개인 홈 경로)는 링크로 만들지 않는다.
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('/') || href.startsWith('\\')) return null;
  // SPEC.md는 프로젝트 루트, HTML은 docs/에 있으므로 상대 경로를 한 단계 올린다.
  return '../' + href.replace(/^\.\//, '');
}

// inline code를 먼저 자리표시자로 바꾼 뒤 문장 전체를 한 번에 처리한다. 조각별로 처리하면
// `**`가 code를 감싸거나 줄을 넘을 때(실 SPEC에 둘 다 있다) 짝이 다른 조각으로 갈라진다.
function inline(text, ctx, opts = {}) {
  const codes = [];
  const masked = text.replace(/(`+)([\s\S]*?[^`])\1(?!`)/g, (_, ticks, body) => {
    const inner = /^ .* $/.test(body) ? body.slice(1, -1) : body;
    const meaning = !opts.noIds && !ctx.inGlossary && ctx.terms && (ctx.terms.code.get(inner) || ctx.terms.plain.get(inner));
    const open = meaning ? `<code class="term" title="${esc(meaning)}" tabindex="0">` : '<code>';
    return `\uE002${codes.push(open + esc(inner) + '</code>') - 1}\uE003`;
  });
  return prose(masked, ctx, opts).replace(/\uE002(\d+)\uE003/g, (_, i) => codes[i]);
}

function prose(raw, ctx, opts) {
  const kept = [];
  const text = raw.replace(/\\([!-/:-@[-`{-~])/g, (_, ch) => `${kept.push(ch) - 1}`);
  const link = /(!?)\[([^\]\n]*)\]\(((?:[^()\s]|\([^()\s]*\))+)\)/g;
  let out = '', last = 0;
  for (let m; (m = link.exec(text));) {
    out += format(esc(text.slice(last, m.index)), ctx, opts);
    const restore = t => t.replace(/\uE000(\d+)\uE001/g, (_, i) => kept[i]);
    const label = format(esc(m[2]), ctx, { ...opts, noIds: true });
    const href = safeHref(restore(m[3]));
    // 이미지는 프로젝트 안의 파일만 끼워 넣는다. 원격 이미지는 열 때 외부 요청이 생기므로 링크로 남긴다.
    if (m[1] && href !== null && !/^(?:https?:|mailto:|#)/i.test(href)) out += `<img src="${esc(href)}" alt="${esc(restore(m[2]))}" loading="lazy">`;
    else out += href === null ? label : `<a href="${esc(href)}">${label}</a>`;
    last = link.lastIndex;
  }
  out += format(esc(text.slice(last)), ctx, opts);
  return out.replace(/(\d+)/g, (_, i) => esc(kept[i]));
}

function format(html, ctx, opts) {
  // 용어 표의 말은 자리표시자로 먼저 감싼다. 뜻(title)에 ID나 `**`가 있어도 아래 치환이 건드리지 않는다.
  const terms = [];
  if (!opts.noIds && !ctx.inGlossary && ctx.terms && ctx.terms.re) html = html.replace(ctx.terms.re, (m, pre, word) =>
    `${pre}\uE020${terms.push(`<span class="term" title="${esc(ctx.terms.text.get(word))}" tabindex="0">${word}</span>`) - 1}\uE021`);
  let s = html
    .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![*\w])/g, '$1<em>$2</em>');
  if (!opts.noIds) s = s.replace(ID, id => {
    if (!ctx.ids.has(id)) return id;
    // 역참조: 어느 카드 본문이 이 ID를 언급하는가. 카드 머리의 "참조됨"이 이것으로 채워진다.
    if (ctx.card && ctx.card !== id && !opts.noRefs) (ctx.refs[id] = ctx.refs[id] || new Set()).add(ctx.card);
    return `<a class="idref" href="#${id}">${id}</a>`;
  });
  return s.replace(/\uE020(\d+)\uE021/g, (_, i) => terms[i]);
}

// ---- blocks -------------------------------------------------------------------------------

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([^`\s]*)[^`]*$/;
// 닫는 표시: 3칸까지 들여쓰고, 여는 표시와 같은 글자로 같거나 더 길게, 뒤에는 공백만(CommonMark).
// 렌더러의 본문·사전 스캔과 준비 검사(project-readiness.js)가 모두 이 두 규칙만 쓴다.
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})\s*$/;
function closesFence(line, fence) { const m = line.match(FENCE_CLOSE); return Boolean(m && m[1][0] === fence[0] && m[1].length >= fence.length); }
// 탭은 4칸으로 본다(render와 같은 기준). 줄마다 fenced 블록 안(여는·닫는 줄 포함)이면 true.
function fencedMask(lines) {
  let fence = null;
  return lines.map(raw => {
    const line = raw.replace(/\t/g, '    ');
    if (fence) { if (closesFence(line, fence)) fence = null; return true; }
    const m = line.match(FENCE);
    if (m) { fence = m[1]; return true; }
    return false;
  });
}
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/;
const RULE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const LIST = /^( *)([-*+]|\d{1,9}[.)])(?: +|$)/;
const QUOTE = /^ {0,3}> ?/;
const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/;
const indentOf = line => line.match(/^ */)[0].length;
const CALLOUT_KIND = { 예외: 'warn', 주의: 'warn', 이유: 'info', 참고: 'info', 예시: 'example' };
const CALLOUT = /^\s*\*\*(예외|주의|이유|참고|예시)\*\*[\s:.]*/;
const WHY = /^\s*이유\s*:\s*/;

function isTableStart(lines, i) {
  return lines[i].trim().startsWith('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes('-');
}
function isBlockStart(lines, i) {
  const line = lines[i];
  return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || LIST.test(line) || isTableStart(lines, i);
}

// 표 한 줄을 셀로 나눈다. inline code 속 `|`와 `\|`는 구분자가 아니다.
function cells(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
  const out = [];
  let cur = '', tick = 0;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === '`') {
      let run = 1;
      while (s[i + run] === '`') run++;
      tick = tick === 0 ? run : (tick === run ? 0 : tick);
      cur += s.slice(i, i + run);
      i += run - 1;
    } else if (ch === '\\' && s[i + 1] === '|') { cur += tick ? '|' : '\\|'; i++; }
    else if (ch === '|' && !tick) { out.push(cur.trim()); cur = ''; }
    else cur += ch;
  }
  out.push(cur.trim());
  return out;
}

function renderTable(rows, ctx) {
  const header = cells(rows[0]);
  const align = cells(rows[1]).map(c => /^:-+:$/.test(c) ? 'center' : /-+:$/.test(c) ? 'right' : /^:-+/.test(c) ? 'left' : '');
  const reqCol = header.findIndex(c => /^(?:Requirement|요구사항)$/i.test(c));
  const statusCol = header.findIndex(c => /^(?:Status|상태)$/i.test(c));
  const trace = reqCol >= 0 && statusCol >= 0;
  // 용어 표 자신에는 뜻 풍선을 달지 않는다(자기 자신을 설명하게 된다).
  ctx.inGlossary = glossaryColumns(header) !== null;
  const cell = (tag, text, i) => {
    const a = align[i] ? ` style="text-align:${align[i]}"` : '';
    const word = text.toLowerCase();
    const body = BADGES.has(word) ? `<span class="badge st-${word}">${esc(word)}</span>` : inline(text, ctx);
    return `<${tag}${a}>${body}</${tag}>`;
  };
  const out = ['<div class="table-wrap"><table>', '<thead><tr>' + header.map((c, i) => cell('th', c, i)).join('') + '</tr></thead>', '<tbody>'];
  for (const row of rows.slice(2)) {
    const values = cells(row);
    while (values.length < header.length) values.push('');
    if (trace) {
      const word = (values[statusCol] || '').toLowerCase();
      if (STATUSES.includes(word)) ctx.statusCounts[word] = (ctx.statusCounts[word] || 0) + 1;
    }
    out.push('<tr>' + values.slice(0, header.length).map((c, i) => cell('td', c, i)).join('') + '</tr>');
  }
  out.push('</tbody>', '</table></div>');
  ctx.inGlossary = false;
  return out.join('\n');
}

function parseList(lines, start, ctx) {
  const first = lines[start].match(LIST);
  const base = first[1].length;
  const ordered = /\d/.test(first[2]);
  const items = [];
  let i = start, loose = false, cur = null, contentIndent = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      const next = lines[i + 1];
      const m = next !== undefined && next.match(LIST);
      if (next !== undefined && next.trim() && (indentOf(next) > base || (m && indentOf(next) === base && /\d/.test(m[2]) === ordered))) {
        if (indentOf(next) === base) loose = true;
        cur.push('');
        i++;
        continue;
      }
      break;
    }
    const m = line.match(LIST);
    const ind = indentOf(line);
    if (m && ind === base) {
      if (/\d/.test(m[2]) !== ordered) break;
      cur = [line.slice(m[0].length)];
      contentIndent = m[0].length;
      items.push(cur);
      i++;
      continue;
    }
    if (ind > base) { cur.push(line.replace(new RegExp('^ {0,' + contentIndent + '}'), '')); i++; continue; }
    // 게으른 이어쓰기: 들여쓰지 않은 다음 줄이 새 블록이 아니면 같은 항목의 문장이다.
    // `이유:` 줄은 목록에 이어 붙지 않고 목록 뒤의 새 문단이 된다.
    if (ind <= base && !m && !isBlockStart(lines, i) && !WHY.test(line) && lines[i - 1].trim()) { cur.push(line.trim()); i++; continue; }
    break;
  }
  const startNo = ordered ? parseInt(first[2], 10) : 1;
  const tag = ordered ? 'ol' : 'ul';
  const out = [`<${tag}${ordered && startNo !== 1 ? ` start="${startNo}"` : ''}>`];
  for (const item of items) {
    const task = !ordered && item[0].match(/^\[([ xX])\]\s+/);
    if (task) item[0] = item[0].slice(task[0].length);
    const parts = renderBlocks(item, ctx, { tight: !loose });
    const cls = task ? ' class="task"' : '';
    const box = task ? `<input type="checkbox" disabled${task[1] === ' ' ? '' : ' checked'}> ` : '';
    out.push(`<li${cls}>${box}${parts.join('\n')}${parts.length > 1 ? '\n' : ''}</li>`);
  }
  out.push(`</${tag}>`);
  return { html: out.join('\n'), next: i };
}

// opts.top: 문서 최상위 — 제목이 절(section)을 열고 닫는다. opts.tight: 목록 항목 속 문단은 <p> 없이.
function renderBlocks(lines, ctx, opts = {}) {
  const out = [];
  let i = 0;
  const closeTo = level => {
    while (ctx.stack.length && ctx.stack[ctx.stack.length - 1].level >= level) out.push(ctx.stack.pop().close || '</section>');
    ctx.card = [...ctx.stack].reverse().find(e => e.card)?.card || null;
  };
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m;
    if ((m = line.match(FENCE))) {
      const fence = m[1];
      const body = [];
      i++;
      while (i < lines.length && !closesFence(lines[i], fence)) body.push(lines[i++]);
      i++;
      const diagram = m[2] === 'flow' ? renderFlow(body, ctx) : null;
      if (diagram) { out.push(diagram); continue; }
      const lang = m[2] ? ` class="language-${esc(m[2])}"` : '';
      out.push(`<pre><code${lang}>${esc(body.join('\n'))}${body.length ? '\n' : ''}</code></pre>`);
      continue;
    }
    if ((m = line.match(HEADING))) {
      const level = m[1].length;
      const text = m[2];
      i++;
      if (level === 1 && opts.top && !ctx.titleSeen) { ctx.titleSeen = true; ctx.title = text; continue; }
      const idm = text.match(/^((?:REQ|NFR|TEST)-[A-Z0-9]+-\d{3})\b[.,:;]?\s*(.*)$/);
      const chapter = level === 2 && text.match(/^(\d+)\.\s/);
      if (opts.top) closeTo(level);
      if (idm && ctx.ids.has(idm[1]) && opts.top && !ctx.opened.has(idm[1])) {
        const id = idm[1];
        ctx.opened.add(id);
        const kind = KIND[id.split('-')[0]][0];
        const name = idm[2] ? inline(idm[2], ctx, { noIds: true }) : '';
        ctx.toc.push(`<li class="toc-id kind-${kind}"><a href="#${id}">${id}${name ? ` <span>${name}</span>` : ''}</a></li>`);
        ctx.stack.push({ level, card: id });
        ctx.card = id;
        out.push(`<section class="card kind-${kind}" id="${id}">`);
        out.push(`<h${level}><span class="idtag">${id}</span>${name ? ' ' + name : ''}</h${level}>`);
        out.push(`\uE010${id}\uE011`);
      } else if (opts.top && ctx.card && level >= 4 && level > ctx.stack.find(e => e.card === ctx.card).level) {
        // 카드 안 소제목(목적·입력·동작…)은 왼쪽 이름표, 오른쪽 내용 칸으로 보인다. 다음 소제목이나 카드 끝에서 닫힌다.
        ctx.stack.push({ level, card: ctx.card, close: '</div></section>' });
        out.push(`<section class="field"><h${level}>${inline(text, ctx, { noIds: true })}</h${level}><div class="field-body">`);
      } else if (level === 2 && opts.top) {
        const sid = chapter ? 'sec-' + chapter[1] : 'part-' + (++ctx.parts);
        ctx.toc.push(`<li class="toc-2"><a href="#${sid}">${inline(text, ctx, { noIds: true })}</a></li>`);
        ctx.stack.push({ level, card: null });
        ctx.card = null;
        out.push(`<section class="chapter" id="${sid}">`);
        out.push(`<h2>${inline(text, ctx, { noIds: true })}</h2>`);
      } else {
        out.push(`<h${level}>${inline(text, ctx, { noIds: true })}</h${level}>`);
      }
      continue;
    }
    if (RULE.test(line)) { out.push('<hr>'); i++; continue; }
    if (isTableStart(lines, i)) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(lines[i++]);
      const head = cells(rows[0]);
      if (opts.top && !ctx.chaptersSeen() && !ctx.meta && head[0] === '항목') {
        ctx.meta = rows.slice(2).map(cells).filter(r => r[0]);
        continue;
      }
      out.push(renderTable(rows, ctx));
      continue;
    }
    if (QUOTE.test(line)) {
      const body = [];
      while (i < lines.length && lines[i].trim() && QUOTE.test(lines[i])) body.push(lines[i++].replace(QUOTE, ''));
      // `> **예외** …`처럼 첫 단어가 굵은 이름표면 그 종류의 색 상자로 보인다.
      const label = body[0].match(CALLOUT);
      if (label) {
        body[0] = body[0].slice(label[0].length);
        out.push(`<aside class="callout callout-${CALLOUT_KIND[label[1]]}"><span class="callout-label">${label[1]}</span>`, ...renderBlocks(body, ctx), '</aside>');
      } else out.push('<blockquote>', ...renderBlocks(body, ctx), '</blockquote>');
      continue;
    }
    if (LIST.test(line)) {
      const list = parseList(lines, i, ctx);
      out.push(list.html);
      i = list.next;
      continue;
    }
    const para = [];
    // `이유:`로 시작하는 줄은 앞 문장에 붙어 있어도 새 문단이다.
    while (i < lines.length && lines[i].trim() && (para.length === 0 || (!isBlockStart(lines, i) && !WHY.test(lines[i])))) para.push(lines[i++]);
    const why = !opts.tight && para[0].match(WHY);
    if (why) para[0] = para[0].slice(why[0].length);
    // 문단 전체를 한 번에 inline 처리한다(줄을 넘는 강조). 줄 끝 두 칸·역슬래시는 강제 줄바꿈이다.
    const joined = para.map((l, k) => {
      const hard = k < para.length - 1 && /(?: {2,}|\\)$/.test(l);
      return l.trim().replace(/\\$/, hard ? '' : '\\') + (hard ? '\uE004' : '');
    }).join('\n');
    const html = inline(joined, ctx).replace(/\uE004/g, '<br>');
    out.push(why ? `<p class="why"><span class="why-label">이유</span> ${html}</p>` : opts.tight ? html : `<p>${html}</p>`);
  }
  if (opts.top) closeTo(0);
  return out;
}

// ---- flow diagram -------------------------------------------------------------------------

// ```flow 블록을 inline SVG 흐름도로 그린다. 외부 JS(Mermaid 등) 없이 결정적으로 배치한다.
// 문법: 한 줄에 `A -> B -> C`, 이름표가 있는 화살표는 `A -(실패)-> B`. 같은 글자면 같은 상자다.
// 배치: 위에서 아래로 층을 쌓는다. 층 = 앞으로 가는 화살표 기준 최장 경로, 같은 층은 처음 나온 순서.
// 되돌아가는 화살표(순환)는 층을 만들지 않고 오른쪽 통로로 점선을 긋는다. 단순 흐름용이라
// 교차 최소화는 하지 않는다. 문법에 맞지 않는 줄이 하나라도 있으면 null — 호출자가 코드 블록으로 둔다.
const FLOW = { h: 36, minW: 72, pad: 24, gapX: 24, gapY: 44, margin: 8, lane: 12, laneStep: 10 };
const ARROW = /\s*-(?:\(([^()\n]*)\)-)?>\s*/;
const flowNum = n => String(Math.round(n * 10) / 10);
// 글자 폭 추정(13px 글꼴): 한글·한자·전각은 13, 그 밖은 7.5. 브라우저 측정 없이 같은 입력이면 같은 폭이다.
const labelWidth = text => [...text].reduce((w, ch) => w + (/[ᄀ-ᇿ⺀-꓏가-힣豈-﫿＀-￯]/.test(ch) ? 13 : 7.5), 0);

function parseFlow(lines) {
  const nodes = [], index = new Map(), edges = [];
  const node = label => { if (!index.has(label)) { index.set(label, nodes.length); nodes.push({ label }); } return index.get(label); };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    const parts = line.split(new RegExp(ARROW.source));
    // split은 [노드, 이름표, 노드, 이름표, 노드 …]를 돌려준다. 노드가 둘 미만이거나 빈 노드가 있으면 문법 밖이다.
    if (parts.length < 3) return null;
    const names = parts.filter((_, k) => k % 2 === 0).map(t => t.trim());
    if (names.some(t => !t)) return null;
    for (let k = 0; k + 1 < names.length; k++) edges.push({ from: node(names[k]), to: node(names[k + 1]), label: (parts[2 * k + 1] || '').trim() });
  }
  return nodes.length ? { nodes, edges } : null;
}

function renderFlow(lines, ctx) {
  const g = parseFlow(lines);
  if (!g) return null;
  const { nodes, edges } = g;
  // 처음 나온 순서로 DFS 하며 스택 위의 노드로 가는 화살표를 되돌림으로 분류한다.
  const state = nodes.map(() => 0), out = nodes.map(() => []);
  edges.forEach((e, k) => out[e.from].push(k));
  const visit = v => {
    state[v] = 1;
    for (const k of out[v]) {
      const t = edges[k].to;
      if (state[t] === 1) edges[k].back = true;
      else if (state[t] === 0) visit(t);
    }
    state[v] = 2;
  };
  nodes.forEach((_, v) => { if (!state[v]) visit(v); });
  // 최장 경로 층: 되돌림을 뺀 그래프는 DAG이므로 층이 더 바뀌지 않을 때까지 완화하면 끝난다.
  const rank = nodes.map(() => 0);
  for (let changed = true; changed;) {
    changed = false;
    for (const e of edges) if (!e.back && rank[e.to] < rank[e.from] + 1) { rank[e.to] = rank[e.from] + 1; changed = true; }
  }
  const rows = [];
  nodes.forEach((n, v) => { n.w = Math.max(FLOW.minW, Math.ceil(labelWidth(n.label)) + FLOW.pad); (rows[rank[v]] = rows[rank[v]] || []).push(v); });
  const rowWidth = row => row.reduce((w, v) => w + nodes[v].w, 0) + FLOW.gapX * (row.length - 1);
  const maxRow = Math.max(...rows.map(rowWidth));
  rows.forEach((row, r) => {
    let x = FLOW.margin + (maxRow - rowWidth(row)) / 2;
    for (const v of row) { Object.assign(nodes[v], { x, y: FLOW.margin + r * (FLOW.h + FLOW.gapY) }); x += nodes[v].w + FLOW.gapX; }
  });
  const backs = edges.filter(e => e.back).length;
  const width = maxRow + 2 * FLOW.margin + (backs ? FLOW.lane + FLOW.laneStep * backs : 0);
  const height = 2 * FLOW.margin + rows.length * FLOW.h + (rows.length - 1) * FLOW.gapY;
  const id = `flow-arrow-${++ctx.flows}`;
  const cx = n => n.x + n.w / 2, cy = n => n.y + FLOW.h / 2;
  const svg = [];
  let lane = 0;
  for (const e of edges) {
    const a = nodes[e.from], b = nodes[e.to];
    let d, lx, ly;
    if (e.back) {
      const x = FLOW.margin + maxRow + FLOW.lane + FLOW.laneStep * lane++;
      const self = a === b ? FLOW.h / 4 : 0;
      d = `M${flowNum(a.x + a.w)} ${flowNum(cy(a) + self)} H${flowNum(x)} V${flowNum(cy(b) - self)} H${flowNum(b.x + b.w)}`;
      lx = x; ly = (cy(a) + cy(b)) / 2;
    } else {
      const [x1, y1, x2, y2] = [cx(a), a.y + FLOW.h, cx(b), b.y];
      d = `M${flowNum(x1)} ${flowNum(y1)} L${flowNum(x2)} ${flowNum(y2)}`;
      lx = (x1 + x2) / 2; ly = (y1 + y2) / 2;
    }
    svg.push(`<path class="edge${e.back ? ' back' : ''}" d="${d}" marker-end="url(#${id})"/>` +
      (e.label ? `<text class="elabel" x="${flowNum(lx)}" y="${flowNum(ly)}">${esc(e.label)}</text>` : ''));
  }
  for (const n of nodes) {
    svg.push(`<g class="node"><rect x="${flowNum(n.x)}" y="${flowNum(n.y)}" width="${n.w}" height="${FLOW.h}" rx="8"/><text x="${flowNum(cx(n))}" y="${flowNum(cy(n))}">${esc(n.label)}</text></g>`);
  }
  const alt = edges.map(e => `${nodes[e.from].label} → ${nodes[e.to].label}`).join(', ');
  return [
    '<figure class="flow">',
    `<svg viewBox="0 0 ${flowNum(width)} ${height}" width="${flowNum(width)}" height="${height}" role="img" aria-label="${esc(alt)}">`,
    `<defs><marker id="${id}" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z"/></marker></defs>`,
    ...svg,
    '</svg>',
    '</figure>',
  ].join('\n');
}

// ---- document -----------------------------------------------------------------------------

// 정의(제목)로 선언된 ID만 링크 대상이다. 주석과 fenced 예시 속 제목은 정의가 아니다.
function definedIds(lines) {
  const ids = new Map();
  const fenced = fencedMask(lines);
  for (let i = 0; i < lines.length; i++) {
    if (fenced[i]) continue;
    const line = lines[i];
    const m = line.match(/^ {0,3}#{2,4}\s+((?:REQ|NFR|TEST)-[A-Z0-9]+-\d{3})\b[.,:;]?[ \t]*(.*?)(?:\s+#+)?\s*$/);
    if (m && !ids.has(m[1])) ids.set(m[1], { title: m[2] });
  }
  return ids;
}

// fenced 예시와 들여쓴 코드 밖의 줄만 돌려준다(줄 번호 대신 줄 배열). 사전 스캔용.
function visibleLines(lines) {
  const fenced = fencedMask(lines);
  return lines.map((line, i) => (fenced[i] ? '' : line));
}

// 추적성 표(Requirement 열이 있는 표)에서 ID별 구현·테스트·상태를 모은다. 기능 목록과 카드 머리가
// 쓰며, 같은 내용을 SPEC에 다시 적지 않아도 되게 한다. 열 이름 규칙은 project-readiness.js와 같다.
// 기능 그룹 표(카테고리·이름 열)는 CATEGORY 코드에 사람이 읽는 이름을 붙인다.
function scanTables(lines) {
  const trace = new Map(), groups = new Map();
  const v = visibleLines(lines);
  for (let i = 0; i < v.length; i++) {
    if (!isTableStart(v, i)) continue;
    const head = cells(v[i]);
    const col = re => head.findIndex(c => re.test(c));
    const req = col(/^(?:Requirement|요구사항)$/i), impl = col(/^(?:Implementation|구현)$/i), test = col(/^(?:Test|테스트)$/i), st = col(/^(?:Status|상태)$/i);
    const cat = col(/^(?:Category|카테고리)$/i), name = col(/^(?:Name|이름)$/i);
    let j = i + 2;
    for (; j < v.length && v[j].trim().startsWith('|'); j++) {
      const row = cells(v[j]);
      if (req >= 0) for (const id of (row[req] || '').match(ID) || []) {
        if (!trace.has(id)) trace.set(id, { impl: impl >= 0 ? row[impl] || '' : '', test: test >= 0 ? row[test] || '' : '', status: st >= 0 ? (row[st] || '').toLowerCase() : '' });
      }
      if (cat >= 0 && name >= 0 && /^[A-Z0-9]+$/.test(row[cat] || '') && row[name]) groups.set(row[cat], row[name]);
    }
    i = j - 1;
  }
  return { trace, groups };
}

// `| 용어 | 뜻 |` 표의 열 위치. 뜻 열은 `뜻`·`설명`·`의미` 중 하나다.
function glossaryColumns(head) {
  const term = head.findIndex(c => /^(?:용어|Term)$/i.test(c)), meaning = head.findIndex(c => /^(?:뜻|설명|의미|Meaning)$/i.test(c));
  return term >= 0 && meaning >= 0 ? { term, meaning } : null;
}

// 용어 표를 모은다. 칸 속 `코드 이름`은 본문의 같은 코드에, 나머지 글자 조각(두 글자 이상)은 본문의 같은 말에
// 뜻을 단다. 뜻은 마크다운 표시를 뺀 글자다. 먼저 나온 정의가 이긴다.
function glossary(lines) {
  const code = new Map(), text = new Map();
  const v = visibleLines(lines);
  for (let i = 0; i < v.length; i++) {
    if (!isTableStart(v, i)) continue;
    const cols = glossaryColumns(cells(v[i]));
    let j = i + 2;
    for (; j < v.length && v[j].trim().startsWith('|'); j++) {
      if (!cols) continue;
      const row = cells(v[j]);
      const meaning = (row[cols.meaning] || '').replace(/`+/g, '').replace(/\*\*|\*/g, '').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').trim();
      const cell = row[cols.term] || '';
      if (!meaning || /\bTBD\b/.test(cell + meaning)) continue;
      for (const m of cell.matchAll(/`([^`]+)`/g)) if (!code.has(m[1].trim())) code.set(m[1].trim(), meaning);
      // "거래 줄(leg)", "잠정값 · 확정값"처럼 괄호·구분자로 적은 다른 이름도 각각 같은 뜻의 용어다.
      for (const part of cell.replace(/`[^`]*`/g, '\u0000').split(/[()（）,·/\u0000]/)) {
        const plain = part.replace(/\s+/g, ' ').trim();
        if ([...plain].length >= 2 && !text.has(plain)) text.set(plain, meaning);
      }
    }
    i = j - 1;
  }
  // 긴 말부터 맞춘다("기준영업일"이 "영업일"보다 먼저). 영문·숫자 용어는 더 긴 단어 속에서는 맞추지 않는다.
  const words = [...text.keys()].sort((a, b) => b.length - a.length || (a < b ? -1 : 1));
  const escRe = w => esc(w).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = words.length ? new RegExp(`(^|[^A-Za-z0-9_])(${words.map(escRe).join('|')})(?![A-Za-z0-9_])`, 'g') : null;
  // 정규식은 escape된 글자에 맞추므로 역참조도 escape된 글자로 찾는다.
  const byEscaped = new Map(words.map(w => [esc(w), text.get(w)]));
  // plain: 용어 표에 글자로 적은 말을 본문에서 `코드`로 쓴 경우(시트 이름 등)에도 뜻을 단다.
  return { code, plain: text, text: byEscaped, re };
}

// ---- plain language -----------------------------------------------------------------------

// 처음 보는 사람이 읽기 어려운 말. AGENTS.md "SPEC 문장 쓰기"의 표와 같은 목록이다(준비 검사가 경고로 쓴다).
// inline code·fenced 블록·주석 속 글자는 코드 이름이므로 보지 않는다. "트리거"는 Apps Script 화면의 실제
// 메뉴 이름이기도 해서 기계적으로 가를 수 없으므로 목록에 넣지 않는다(작성 기준 표에만 있다).
const PLAIN_WORDS = [
  ['단조 증가', /단조\s*(?:증가|감소|롭게\s*증가)/g], ['append-only', /append[- ]only/gi], ['멱등', /멱등|idempoten\w*/gi],
  ['fallback', /fallback|폴백/gi], ['정합성', /정합성|무결성/g], ['원복', /원복|롤백|rollback/gi], ['관측', /관측/g],
  ['스키마', /스키마|schema/gi], ['dedupe', /dedupe|디듀프/gi], ['영속', /영속|불변/g],
];
const LONG_PARAGRAPH = 300;

// 쉬운 말 기준 검사. 판정이 아니라 다시 읽어 볼 곳을 알려 준다. 검사한 문단 수를 함께 돌려주어
// "0건"이 아무것도 보지 못한 결과와 구별되게 한다.
function plainLanguage(markdown) {
  const src = normalize(markdown).replace(/<!--[\s\S]*?(?:-->|$)/g, c => c.replace(/[^\n]/g, ' '));
  const lines = visibleLines(src.split('\n'));
  const words = new Map(), long = [];
  let paragraphs = 0, block = null;
  const close = () => {
    if (!block) return;
    paragraphs++;
    if (block.chars > LONG_PARAGRAPH) long.push({ line: block.line, chars: block.chars });
    block = null;
  };
  lines.forEach((line, k) => {
    const text = line.replace(/`[^`\n]*`/g, ' ').replace(/\]\([^)]*\)/g, ']');
    for (const [name, re] of PLAIN_WORDS) for (const _ of text.matchAll(re)) {
      const w = words.get(name) || words.set(name, { count: 0, line: k + 1 }).get(name);
      w.count++;
    }
    const t = line.trim();
    const item = LIST.test(line) && !/^ {2,}/.test(line.match(LIST)[1]);
    if (!t || HEADING.test(line) || t.startsWith('|') || item || QUOTE.test(line)) close();
    if (!t || HEADING.test(line) || t.startsWith('|')) return;
    const body = t.replace(LIST, '').replace(QUOTE, '');
    if (!block) block = { line: k + 1, chars: 0 };
    block.chars += [...body].length;
  });
  close();
  return { words: [...words].map(([word, v]) => ({ word, count: v.count, line: v.line })), long, paragraphs, limit: LONG_PARAGRAPH };
}

// CHANGELOG.md에서 ID를 언급한 목록 항목을 모은다. 예시 블록(fenced)은 이력이 아니다.
// 각 항목에는 가장 가까운 `##`(버전·날짜)과 `###`(Added/Changed/Fixed) 제목을 붙인다.
function changelogHistory(markdown) {
  const history = new Map();
  if (!markdown) return history;
  const lines = visibleLines(normalize(markdown).replace(/<!--[\s\S]*?(?:-->|$)/g, '').split('\n'));
  let version = '', type = '', item = null;
  const flush = () => {
    if (!item) return;
    const text = item.join('\n');
    const where = [version, type].filter(Boolean).join(' · ');
    for (const id of new Set(text.match(ID) || [])) (history.get(id) || history.set(id, []).get(id)).push({ where, text });
    item = null;
  };
  for (const line of lines) {
    let m;
    if ((m = line.match(/^ {0,3}(#{2,3})\s+(.*?)\s*#*\s*$/))) { flush(); if (m[1] === '##') { version = m[2]; type = ''; } else type = m[2]; continue; }
    if ((m = line.match(/^ {0,3}[-*+]\s+(.*)$/))) { flush(); item = [m[1]]; continue; }
    if (item && line.trim() && /^\s/.test(line)) { item.push(line.trim()); continue; }
    flush();
  }
  flush();
  return history;
}

function cardMeta(id, ctx) {
  const t = ctx.trace.get(id) || {};
  const parts = [];
  if (STATUSES.includes(t.status)) parts.push(`<span class="badge st-${t.status}">${t.status}</span>`);
  if (t.impl) parts.push(`<span class="meta"><b>구현</b> ${inline(t.impl, ctx, { noIds: true })}</span>`);
  if (t.test) parts.push(`<span class="meta"><b>테스트</b> ${inline(t.test, ctx, { noRefs: true })}</span>`);
  const refs = [...(ctx.refs[id] || [])].sort();
  if (refs.length) parts.push(`<span class="meta"><b>참조됨</b> ${refs.map(r => `<a class="idref" href="#${r}">${r}</a>`).join(', ')}</span>`);
  const hist = ctx.history.get(id) || [];
  // 이력이 빈 요구사항은 빈칸이 누락처럼 보이므로 "기록 없음"을 밝힌다. 판단 근거(CHANGELOG)가 없거나
  // 검증 절차(TEST)이면 말하지 않는다.
  if (!hist.length && ctx.hasChangelog && !id.startsWith('TEST-')) parts.push('<span class="meta nohist"><b>변경 이력</b> CHANGELOG에 이 ID로 기록된 변경 없음</span>');
  let details = '';
  if (hist.length) {
    details = `<details class="history"><summary>변경 이력 ${hist.length}</summary>\n<ul>\n` +
      hist.map(h => `<li>${h.where ? `<span class="hist">${esc(h.where)}</span> ` : ''}${inline(h.text, ctx, { noRefs: true })}</li>`).join('\n') + '\n</ul>\n</details>';
  }
  if (!parts.length && !details) return '';
  return (parts.length ? `<div class="card-meta">${parts.join('')}</div>` : '') + (details ? (parts.length ? '\n' : '') + details : '');
}

// 기능 목록: 번호만 보고도 무슨 기능인지 알 수 있게 CATEGORY별로 ID·이름·상태·구현·테스트를 한 표로 모은다.
// TEST는 기능이 아니라 검증 절차이므로 넣지 않는다.
function featureIndex(ctx) {
  const byCat = new Map();
  for (const [id, def] of ctx.ids) {
    const [kind, cat] = id.split('-');
    if (kind === 'TEST') continue;
    const key = kind === 'NFR' ? 'NFR ' + cat : cat;
    (byCat.get(key) || byCat.set(key, []).get(key)).push([id, def]);
  }
  if (!byCat.size) return '';
  const out = ['<section class="chapter" id="feature-index">', '<h2>기능 목록</h2>',
    '<div class="table-wrap"><table class="index">', '<thead><tr><th>ID</th><th>이름</th><th>상태</th><th>구현</th><th>테스트</th></tr></thead>', '<tbody>'];
  for (const [key, rows] of byCat) {
    const cat = key.replace(/^NFR /, '');
    const name = ctx.groups.get(cat);
    out.push(`<tr class="group"><th colspan="5"><span class="gcode">${esc(key)}</span>${name ? ' ' + inline(name, ctx, { noIds: true }) : ''}</th></tr>`);
    for (const [id, def] of rows) {
      const t = ctx.trace.get(id) || {};
      const st = STATUSES.includes(t.status) ? `<span class="badge st-${t.status}">${t.status}</span>` : '';
      out.push(`<tr><td><a class="idref" href="#${id}">${id}</a></td><td>${def.title ? inline(def.title, ctx, { noIds: true }) : '<span class="untitled">이름 없음</span>'}</td><td>${st}</td><td>${t.impl ? inline(t.impl, ctx, { noIds: true }) : ''}</td><td>${t.test ? inline(t.test, ctx, { noRefs: true }) : ''}</td></tr>`);
    }
  }
  out.push('</tbody>', '</table></div>');
  out.push('</section>');
  return out.join('\n');
}

function render(markdown, options = {}) {
  const text = normalize(markdown).replace(/<!--[\s\S]*?(?:-->|$)/g, '');
  const lines = text.replace(/\t/g, '    ').split('\n');
  const { trace, groups } = scanTables(lines);
  const ctx = { ids: definedIds(lines), stack: [], toc: [], opened: new Set(), statusCounts: {}, meta: null, title: null, titleSeen: false, parts: 0,
    card: null, refs: {}, trace, groups, history: changelogHistory(options.changelog || ''), hasChangelog: Boolean(options.changelog), flows: 0,
    terms: glossary(lines), inGlossary: false };
  ctx.chaptersSeen = () => ctx.toc.some(t => t.startsWith('<li class="toc-2"'));
  const rendered = renderBlocks(lines, ctx, { top: true }).join('\n');
  // 카드 머리는 본문 전체를 읽은 뒤에야 역참조를 알 수 있으므로 자리표시자를 마지막에 채운다.
  ctx.card = null;
  const body = rendered.replace(/\uE010([A-Z0-9-]+)\uE011\n?/g, (_, id) => { const m = cardMeta(id, ctx); return m ? m + '\n' : ''; });
  const index = featureIndex(ctx);
  if (index) ctx.toc.unshift('<li class="toc-2"><a href="#feature-index">기능 목록</a></li>');
  const title = options.title || ctx.title || 'SPEC';
  const counts = { REQ: 0, NFR: 0, TEST: 0 };
  for (const id of ctx.ids.keys()) counts[id.split('-')[0]]++;
  const stats = Object.entries(counts).map(([k, n]) => `<div class="stat"><b>${n}</b><span>${KIND[k][1]}</span></div>`)
    .concat(STATUSES.filter(s => ctx.statusCounts[s]).map(s => `<div class="stat st-${s}"><b>${ctx.statusCounts[s]}</b><span>${s}</span></div>`));
  const chips = (ctx.meta || []).map(([k, v = '']) => {
    const word = v.toLowerCase();
    const value = BADGES.has(word) ? `<span class="badge st-${word}">${esc(word)}</span>` : inline(v, ctx, { noIds: true });
    return `<span class="chip"><span>${inline(k, ctx, { noIds: true })}</span><b>${value}</b></span>`;
  });
  return [
    '<!doctype html>',
    '<html lang="ko">',
    '<head>',
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    `<meta name="generator" content="botyard render-spec-html ${RENDERER_VERSION}">`,
    `<meta name="spec-html-renderer" content="${RENDERER_VERSION}">`,
    `<meta name="spec-source-sha256" content="${sourceHash(markdown, options.changelog || '')}">`,
    `<title>${esc(title)}</title>`,
    `<style>${CSS}</style>`,
    '</head>',
    '<body>',
    '<header class="top">',
    `<h1>${inline(title, ctx, { noIds: true })}</h1>`,
    chips.length ? `<div class="chips">${chips.join('')}</div>` : '',
    `<div class="stats">${stats.join('')}</div>`,
    '</header>',
    '<div class="layout">',
    '<nav class="toc" aria-label="목차">',
    '<input type="search" id="toc-filter" placeholder="목차·ID 검색" aria-label="목차 검색">',
    `<ul>${ctx.toc.join('\n')}</ul>`,
    '</nav>',
    `<main>\n${index ? index + '\n' : ''}${body}\n</main>`,
    '</div>',
    `<footer>이 페이지는 <code>SPEC.md</code>에서 생성된 사본입니다. 직접 고치지 말고 <code>SPEC.md</code>를 고친 뒤 <code>${REGENERATE}</code>를 실행하세요.</footer>`,
    `<script>${SCRIPT}</script>`,
    '</body>',
    '</html>',
    '',
  ].filter(l => l !== '').join('\n') + '\n';
}

function readMeta(html) {
  const hash = html.match(/<meta name="spec-source-sha256" content="([a-f0-9]{64})">/);
  const version = html.match(/<meta name="spec-html-renderer" content="([^"]+)">/);
  return hash && version ? { hash: hash[1], version: version[1] } : null;
}

// ---- project ------------------------------------------------------------------------------

function readChangelog(root) {
  const file = path.join(root, 'CHANGELOG.md');
  const st = lstat(file);
  return st && st.isFile() ? fs.readFileSync(file, 'utf8') : '';
}

function lstat(p) { try { return fs.lstatSync(p); } catch (e) { if (e.code === 'ENOENT') return null; throw e; } }

function status(projectRoot) {
  const root = path.resolve(projectRoot);
  const specPath = path.join(root, 'SPEC.md');
  const output = path.join(root, OUTPUT);
  const s = lstat(specPath);
  if (!s || !s.isFile()) return { status: 'NO_SPEC', output: OUTPUT };
  const o = lstat(output);
  if (!o) return { status: 'MISSING', output: OUTPUT };
  if (o.isSymbolicLink() || !o.isFile()) return { status: 'UNMANAGED', output: OUTPUT };
  const current = fs.readFileSync(output, 'utf8');
  const meta = readMeta(current);
  if (!meta) return { status: 'UNMANAGED', output: OUTPUT };
  const spec = fs.readFileSync(specPath, 'utf8');
  const changelog = readChangelog(root);
  if (meta.hash !== sourceHash(spec, changelog)) return { status: 'STALE', output: OUTPUT, version: meta.version };
  // 이전 판 렌더러가 만든 HTML은 바이트를 비교할 수 없다(레이아웃이 다르다) — 호출자가 경고로 알린다.
  if (meta.version !== RENDERER_VERSION) return { status: 'CURRENT', output: OUTPUT, version: meta.version };
  // 해시 한 줄만 맞추고 본문을 손으로 고친 파일도 낡은 것이다. 다시 만들어 바이트로 비교한다.
  const expected = render(spec, { changelog });
  const cards = (expected.match(/<section class="card /g) || []).length;
  if (current !== expected) return { status: 'STALE', output: OUTPUT, version: meta.version, reason: 'content', cards };
  return { status: 'CURRENT', output: OUTPUT, version: meta.version, cards };
}

function write(projectRoot) {
  const root = path.resolve(projectRoot);
  const specPath = path.join(root, 'SPEC.md');
  const s = lstat(specPath);
  if (!s || !s.isFile()) return { status: 'NO_SPEC', output: OUTPUT };
  const docs = path.join(root, 'docs');
  const d = lstat(docs);
  if (d && d.isSymbolicLink()) throw new Error('refusing symlink: docs/');
  if (d && !d.isDirectory()) throw new Error('expected directory: docs/');
  const output = path.join(root, OUTPUT);
  const o = lstat(output);
  if (o && o.isSymbolicLink()) throw new Error('refusing symlink: ' + OUTPUT);
  const html = render(fs.readFileSync(specPath, 'utf8'), { changelog: readChangelog(root) });
  if (o) {
    const current = fs.readFileSync(output, 'utf8');
    if (!readMeta(current)) return { status: 'UNMANAGED', output: OUTPUT };
    if (current === html) return { status: 'CURRENT', output: OUTPUT };
  }
  fs.mkdirSync(docs, { recursive: true });
  const temporary = output + '.tmp-' + process.pid + '-' + crypto.randomBytes(4).toString('hex');
  fs.writeFileSync(temporary, html, { flag: 'wx' });
  try { fs.renameSync(temporary, output); } catch (e) { fs.rmSync(temporary, { force: true }); throw e; }
  return { status: 'WRITTEN', output: OUTPUT };
}

function main(args) {
  const roots = args.filter(a => !a.startsWith('-'));
  const unknown = args.filter(a => a.startsWith('-') && a !== '--check');
  if (roots.length !== 1 || unknown.length) {
    console.error('usage: render-spec-html.js <project-root> [--check]');
    return 2;
  }
  if (args.includes('--check')) {
    const r = status(roots[0]);
    console.log(`SPEC_HTML ${r.status} ${r.output}${r.status === 'CURRENT' ? '' : ' — 재생성: ' + REGENERATE}`);
    return r.status === 'CURRENT' ? 0 : 1;
  }
  const r = write(roots[0]);
  const note = { UNMANAGED: ' — 생성기가 만들지 않은 파일이라 덮어쓰지 않았다. 옮기거나 지운 뒤 다시 실행한다', NO_SPEC: ' — SPEC.md가 없다' }[r.status] || '';
  console.log(`SPEC_HTML ${r.status} ${r.output}${note}`);
  return ['WRITTEN', 'CURRENT'].includes(r.status) ? 0 : 1;
}

const CSS = `
:root{--bg:#f7f8fa;--panel:#fff;--text:#1d2330;--muted:#5d6678;--line:#e2e6ee;--accent:#2f6fde;--code:#f1f3f7;
--req:#2f6fde;--nfr:#8a4fd8;--test:#15907a;--draft:#8a93a6;--implemented:#c98a12;--verified:#1f9d55;--deprecated:#c2413a;--active:#2f6fde;
--warn-bg:#fdf6e7;--info-bg:#eef4fe;--ex-bg:#ecf8f1;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#12151c;--panel:#1a1f29;--text:#e4e8f0;--muted:#9aa4b7;--line:#2c3444;--accent:#6ea0ff;--code:#232a37;
--req:#6ea0ff;--nfr:#b58cff;--test:#3cc4a6;--draft:#8f99ad;--implemented:#e3a93a;--verified:#45c47c;--deprecated:#ef6b62;--active:#6ea0ff;
--warn-bg:#2a2518;--info-bg:#1a2436;--ex-bg:#172a21;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR","Segoe UI",sans-serif;word-break:keep-all;overflow-wrap:break-word}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
code{font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code);border-radius:4px;padding:.1em .35em}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow:auto}pre code{background:none;padding:0}
.top{background:var(--panel);border-bottom:1px solid var(--line);padding:24px 32px}
.top h1{margin:0 0 12px;font-size:26px;line-height:1.3}
.chips,.stats{display:flex;flex-wrap:wrap;gap:8px}.chips{margin-bottom:14px}
.chip{display:inline-flex;gap:6px;align-items:center;border:1px solid var(--line);border-radius:999px;padding:3px 12px;font-size:13px}
.chip>span{color:var(--muted)}
.stat{border:1px solid var(--line);border-radius:10px;padding:8px 14px;min-width:96px;background:var(--bg)}
.stat b{display:block;font-size:22px;line-height:1.2}.stat span{font-size:12px;color:var(--muted)}
.stat.st-draft b{color:var(--draft)}.stat.st-implemented b{color:var(--implemented)}.stat.st-verified b{color:var(--verified)}.stat.st-deprecated b{color:var(--deprecated)}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr);gap:28px;max-width:1280px;margin:0 auto;padding:24px 32px}
.toc{position:sticky;top:16px;align-self:start;max-height:calc(100vh - 32px);overflow:auto;font-size:13px}
.toc input{width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--text);margin-bottom:10px;font:inherit}
.toc ul{list-style:none;margin:0;padding:0}.toc li{margin:1px 0}.toc a{display:block;padding:2px 8px;border-radius:6px;color:var(--text)}
.toc a:hover{background:var(--code);text-decoration:none}
.toc .toc-2{margin-top:8px;font-weight:600}.toc .toc-id a{padding-left:20px;font:12px/1.6 ui-monospace,Menlo,Consolas,monospace;color:var(--muted)}
main{min-width:0}
.chapter{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:4px 24px 16px;margin-bottom:20px}
.chapter>h2{font-size:20px;border-bottom:1px solid var(--line);padding-bottom:8px}
.card{border:1px solid var(--line);border-left:4px solid var(--req);border-radius:8px;padding:2px 16px 8px;margin:14px 0;background:var(--bg)}
.card.kind-nfr{border-left-color:var(--nfr)}.card.kind-test{border-left-color:var(--test)}
.card h3,.card h4{margin:12px 0 4px}.card h4{font-size:14px;color:var(--muted)}
.card{padding:4px 20px 12px}.card p,.card li{max-width:46em}.card p{margin:.4em 0 .9em;line-height:1.85}
.field{display:grid;grid-template-columns:7.5em minmax(0,1fr);gap:0 18px;border-top:1px solid var(--line);padding:10px 0 2px}
.field>h4,.field>h5,.field>h6{margin:.15em 0 0;font-size:13px;font-weight:700;color:var(--muted);letter-spacing:.02em}
.field-body>:first-child{margin-top:0}.field-body>:last-child{margin-bottom:.4em}
.field-body ol{list-style:none;counter-reset:step;padding-left:0}
.field-body ol>li{counter-increment:step;position:relative;padding-left:2.1em;margin:.45em 0}
.field-body ol>li::before{content:counter(step);position:absolute;left:0;top:.2em;width:1.5em;height:1.5em;border-radius:50%;background:var(--code);color:var(--accent);font-size:12px;font-weight:700;line-height:1.5em;text-align:center}
.field-body ul{padding-left:1.2em}.field-body li{margin:.3em 0}
.callout{display:block;margin:12px 0;padding:10px 14px 10px 14px;border-radius:8px;border:1px solid var(--line);border-left:4px solid var(--muted);background:var(--panel)}
.callout>p{margin:.3em 0}.callout-label{display:inline-block;font-size:12px;font-weight:700;border-radius:6px;padding:0 8px;margin-bottom:2px;color:#fff;background:var(--muted)}
.callout-warn{border-left-color:var(--implemented);background:var(--warn-bg)}.callout-warn .callout-label{background:var(--implemented)}
.callout-info{border-left-color:var(--accent);background:var(--info-bg)}.callout-info .callout-label{background:var(--accent)}
.callout-example{border-left-color:var(--verified);background:var(--ex-bg)}.callout-example .callout-label{background:var(--verified)}
p.why{color:var(--muted);border-left:3px solid var(--line);padding-left:10px}.why-label{font-size:12px;font-weight:700;color:var(--accent);margin-right:4px}
.term{text-decoration:underline dotted var(--muted);text-underline-offset:3px;cursor:help}code.term{text-decoration-color:var(--accent)}
.idtag{font:600 13px/1.4 ui-monospace,Menlo,Consolas,monospace;background:var(--req);color:#fff;border-radius:6px;padding:2px 8px}
.kind-nfr .idtag{background:var(--nfr)}.kind-test .idtag{background:var(--test)}
.idref{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.92em;white-space:nowrap}
.idtag,.badge{white-space:nowrap}
.card-meta{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;font-size:13px;margin:2px 0 6px}
.meta b{color:var(--muted);font-weight:600;margin-right:4px}
details.history{font-size:13px;margin:4px 0 8px}details.history summary{cursor:pointer;color:var(--muted)}
details.history ul{margin:6px 0;padding-left:18px}.hist{color:var(--muted);font-size:12px}
tr.group th{background:var(--bg);text-align:left;font-size:14px;padding-top:12px}.gcode{font:600 12px/1.4 ui-monospace,Menlo,Consolas,monospace;border:1px solid var(--line);border-radius:6px;padding:1px 7px;margin-right:4px}
table.index td:first-child{white-space:nowrap}table.index td:nth-child(2){min-width:10em}table.index td:nth-child(4){min-width:13em}.untitled{color:var(--muted);font-style:italic}
.toc .toc-id span{font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;color:var(--text)}
.toc a.active{background:var(--code);color:var(--accent);font-weight:600;box-shadow:inset 3px 0 0 var(--accent)}.toc a.in-chapter{color:var(--accent)}
figure.flow{margin:12px 0;overflow-x:auto}figure.flow svg{display:block;max-width:100%;height:auto;margin:0 auto}
.flow .node rect{fill:var(--panel);stroke:var(--accent);stroke-width:1.5}
.flow text{font-size:13px;fill:var(--text);text-anchor:middle;dominant-baseline:central}
.flow .edge{fill:none;stroke:var(--muted);stroke-width:1.5}.flow .edge.back{stroke-dasharray:5 4}.flow marker path{fill:var(--muted)}
.flow .elabel{font-size:12px;fill:var(--muted);paint-order:stroke;stroke:var(--panel);stroke-width:4px;stroke-linejoin:round}
.nohist{color:var(--muted)}
main img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:8px;background:#fff}
.table-wrap{overflow-x:auto;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
th{background:var(--code);font-weight:600}
.badge{display:inline-block;border-radius:999px;padding:0 10px;font-size:12px;font-weight:600;color:#fff;background:var(--draft)}
.st-implemented.badge{background:var(--implemented)}.st-verified.badge{background:var(--verified)}.st-deprecated.badge{background:var(--deprecated)}.st-active.badge{background:var(--active)}
blockquote{margin:10px 0;padding:4px 16px;border-left:4px solid var(--line);color:var(--muted)}
li.task{list-style:none;margin-left:-1.2em}
hr{border:0;border-top:1px solid var(--line);margin:20px 0}
footer{max-width:1280px;margin:0 auto;padding:8px 32px 40px;color:var(--muted);font-size:13px}
.hidden{display:none}
@media (max-width:860px){.field{grid-template-columns:1fr}.layout{grid-template-columns:1fr;padding:16px}.toc{position:static;max-height:260px}.top{padding:18px 16px}.chapter{padding:2px 14px 12px}footer{padding:8px 16px 32px}}
@media print{.toc,footer{display:none}.layout{display:block;padding:0}.chapter{break-inside:auto;border:0}.card{break-inside:avoid}body{background:#fff}}
`.trim();

// 지금 읽는 곳 = 화면 위쪽 기준선(offset)을 이미 지난 마지막 절. 맨 위에서는 -1(표시 없음),
// 페이지 끝에 닿으면 마지막 절(짧은 마지막 절은 기준선에 닿기 전에 스크롤이 끝난다).
// 페이지 스크립트에 이 함수의 소스를 그대로 넣는다 — 테스트한 판정과 브라우저의 판정이 같은 코드다.
function activeIndex(tops, offset, atBottom) {
  if (atBottom && tops.length) return tops.length - 1;
  var found = -1;
  for (var i = 0; i < tops.length; i++) if (tops[i] <= offset) found = i;
  return found;
}

const SCRIPT = `
(function(){var f=document.getElementById('toc-filter');if(!f)return;f.addEventListener('input',function(){var q=f.value.trim().toLowerCase();
document.querySelectorAll('.toc li').forEach(function(li){li.classList.toggle('hidden',q!==''&&li.textContent.toLowerCase().indexOf(q)<0);});});})();
${activeIndex.toString()}
(function(){var toc=document.querySelector('.toc');if(!toc)return;
var links=[].slice.call(toc.querySelectorAll('a[href^="#"]'));
var targets=links.map(function(a){return document.getElementById(decodeURIComponent(a.getAttribute('href').slice(1)));});
var current=-2;
function chapterOf(i){for(var j=i;j>=0;j--)if(links[j].parentNode.classList.contains('toc-2'))return j;return -1;}
// requestAnimationFrame에 기대지 않는다 — 숨은 탭에서는 실행되지 않아 표시가 멈춘다. 링크 수십 개의 위치만 읽으므로 스크롤마다 계산해도 가볍다.
function update(){
var tops=targets.map(function(t){return t?t.getBoundingClientRect().top:Infinity;});
var doc=document.documentElement,bottom=window.innerHeight+window.pageYOffset>=doc.scrollHeight-2;
var i=activeIndex(tops,96,bottom);if(i===current)return;current=i;
links.forEach(function(a){a.classList.remove('active');a.classList.remove('in-chapter');a.removeAttribute('aria-current');});
if(i<0)return;var a=links[i];a.classList.add('active');a.setAttribute('aria-current','location');
var c=chapterOf(i);if(c>=0&&c!==i)links[c].classList.add('in-chapter');
if(toc.scrollHeight>toc.clientHeight){var r=a.getBoundingClientRect(),t=toc.getBoundingClientRect();
if(r.top<t.top+40||r.bottom>t.bottom-8)toc.scrollTop+=r.top-t.top-toc.clientHeight/3;}}
window.addEventListener('scroll',update,{passive:true});window.addEventListener('resize',update);window.addEventListener('hashchange',update);update();})();
`.trim();

module.exports = { RENDERER_VERSION, OUTPUT, REGENERATE, sourceHash, render, readMeta, status, write, activeIndex, plainLanguage, FENCE, closesFence, fencedMask, cells };

if (require.main === module) {
  try { process.exitCode = main(process.argv.slice(2)); }
  catch (e) { console.error('ERROR spec html: ' + e.message); process.exitCode = 2; }
}
