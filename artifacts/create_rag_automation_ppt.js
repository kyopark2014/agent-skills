// Bedrock Data Automation (BDA) RAG 구현 - PPT 생성 스크립트
// AWS reInvent 2023 테마 기반

const PPTX_PATH = require.resolve('pptxgenjs', { paths: ['/Users/ksdyb/.nvm/versions/node/v24.9.0/lib/node_modules'] });
const SHARP_PATH = require.resolve('sharp', { paths: ['/Users/ksdyb/.nvm/versions/node/v24.9.0/lib/node_modules'] });
const PptxGenJS = require(PPTX_PATH);
const sharp = require(SHARP_PATH);
const fs = require('fs');
const path = require('path');

const ASSETS = '/Users/ksdyb/Documents/src/agent-skills/artifacts/myslide-assets';
const OUT = '/Users/ksdyb/Documents/src/agent-skills/artifacts/rag-automation-bda.pptx';

// --- AWS 테마 컬러 ---
const C = {
  bgBase: '09051B',
  white: 'FFFFFF',
  orange: 'F66C02',
  darkNavy: '161E2D',
  lightSlate: 'C8D0D8',
  magenta: 'C91F8A',
  purple: '5600C2',
  neonPink: 'FF28EF',
};

// 배경 이미지 base64
const bgTitle = 'image/png;base64,' + fs.readFileSync(path.join(ASSETS, 'bg-title.png')).toString('base64');
const bgSection = 'image/png;base64,' + fs.readFileSync(path.join(ASSETS, 'bg-section.png')).toString('base64');
const bgContent = 'image/png;base64,' + fs.readFileSync(path.join(ASSETS, 'bg-content.png')).toString('base64');

const mkShadow = () => ({ type: 'outer', color: '000000', blur: 10, offset: 3, angle: 90, opacity: 0.4 });

const pres = new PptxGenJS();
pres.layout = 'LAYOUT_WIDE'; // 13.33 x 7.5
pres.title = 'Bedrock Data Automation 기반 RAG 구현';

// 공통 footer
function addFooter(slide, num, total) {
  slide.addText('Bedrock Data Automation (BDA) RAG', {
    x: 0.4, y: 7.15, w: 8, h: 0.25,
    fontSize: 9, color: C.lightSlate, fontFace: 'Arial',
  });
  slide.addText(`${num} / ${total}`, {
    x: 12.5, y: 7.15, w: 0.6, h: 0.25,
    fontSize: 9, color: C.lightSlate, align: 'right', fontFace: 'Arial',
  });
}

const TOTAL = 18;

// =====================================================================
// 1. Title slide
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgTitle };

  s.addText('Bedrock Data Automation', {
    x: 0.6, y: 2.2, w: 12, h: 0.7,
    fontSize: 28, bold: true, color: C.orange, fontFace: 'Arial',
  });
  s.addText('(BDA)를 이용한 RAG 구현', {
    x: 0.6, y: 2.85, w: 12, h: 0.9,
    fontSize: 44, bold: true, color: C.white, fontFace: 'Arial',
  });

  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.6, y: 3.95, w: 0.9, h: 0.05, fill: { color: C.orange }, line: { color: C.orange, width: 0 },
  });

  s.addText('멀티모달 콘텐츠 파싱 · Knowledge Bases · 인프라 자동 배포', {
    x: 0.6, y: 4.15, w: 12, h: 0.5,
    fontSize: 20, color: C.lightSlate, fontFace: 'Arial',
  });

  s.addText('GitHub: kyopark2014/rag-automation', {
    x: 0.6, y: 6.4, w: 8, h: 0.4,
    fontSize: 14, color: C.white, fontFace: 'Arial',
  });
  s.addText('발표자  ·  Solutions Architect  ·  Amazon Web Services', {
    x: 0.6, y: 6.75, w: 8, h: 0.3,
    fontSize: 12, color: C.lightSlate, fontFace: 'Arial',
  });
}

// =====================================================================
// 2. Agenda
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('Agenda', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });

  const items = [
    ['01', 'BDA 개요와 RAG 통합'],
    ['02', '파서 옵션 비교'],
    ['03', 'BDA가 처리하는 데이터 유형'],
    ['04', '파일 처리 제한 사항'],
    ['05', '표준 출력 vs 커스텀 출력'],
    ['06', 'API 구성 방법 (ParsingConfiguration)'],
    ['07', 'AWS CLI Knowledge Base 생성 예시'],
    ['08', 'Cross-Region Inference (CRIS)'],
    ['09', 'IAM 권한 / 멀티모달 스토리지'],
    ['10', '임베딩 모델 조합 가이드'],
    ['11', '주요 활용 사례'],
    ['12', '프로젝트 구조 / 실행 흐름'],
    ['13', '설치 및 실행 / 문제 해결'],
  ];

  // 두 컬럼 레이아웃
  const colW = 5.8;
  items.forEach((it, idx) => {
    const col = Math.floor(idx / 7);
    const row = idx % 7;
    const x = 0.6 + col * (colW + 0.4);
    const y = 1.45 + row * 0.72;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w: colW, h: 0.62, rectRadius: 0.08,
      fill: { color: C.darkNavy }, line: { color: C.darkNavy, width: 0 },
      shadow: mkShadow(),
    });
    s.addText(it[0], {
      x: x + 0.15, y: y, w: 0.6, h: 0.62,
      fontSize: 18, bold: true, color: C.orange, valign: 'middle', fontFace: 'Arial',
    });
    s.addText(it[1], {
      x: x + 0.85, y: y, w: colW - 1, h: 0.62,
      fontSize: 15, color: C.white, valign: 'middle', fontFace: 'Arial',
    });
  });
  addFooter(s, 2, TOTAL);
}

// =====================================================================
// 3. BDA 개요 (Content Card)
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('BDA 개요', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });

  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.6, y: 1.4, w: 12.1, h: 2.0, rectRadius: 0.10,
    fill: { color: C.darkNavy }, line: { color: C.magenta, width: 1.5 },
    shadow: mkShadow(),
  });
  s.addText([
    { text: 'Amazon Bedrock Data Automation', options: { color: C.orange, bold: true, fontSize: 20 } },
    { text: '은 문서·이미지·영상·오디오 등 ', options: { color: C.white, fontSize: 18 } },
    { text: '비정형 콘텐츠', options: { color: C.orange, bold: true, fontSize: 18 } },
    { text: '에서 가치 있는 인사이트를 추출하는 ', options: { color: C.white, fontSize: 18 } },
    { text: '완전 관리형 클라우드 서비스', options: { color: C.orange, bold: true, fontSize: 18 } },
    { text: '입니다.', options: { color: C.white, fontSize: 18 } },
  ], { x: 0.95, y: 1.6, w: 11.4, h: 1.6, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.3 });

  // 3가지 핵심 카드
  const cards = [
    ['Multimodal', '문서 / 이미지 / 오디오 / 비디오를\n구조화된 데이터로 변환'],
    ['Single API', '복잡한 모델 오케스트레이션 없이\n단일 API 호출로 처리'],
    ['RAG Quality ↑', 'Knowledge Bases 파서로 적용 시\n검색 품질이 크게 향상'],
  ];
  const cw = 3.93, gap = 0.2;
  cards.forEach((c, i) => {
    const x = 0.6 + i * (cw + gap);
    const y = 3.7;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w: cw, h: 3.0, rectRadius: 0.12,
      fill: { color: C.darkNavy }, line: { color: C.orange, width: 1.5 },
      shadow: mkShadow(),
    });
    s.addText(c[0], {
      x: x + 0.3, y: y + 0.3, w: cw - 0.6, h: 0.6,
      fontSize: 22, bold: true, color: C.orange, fontFace: 'Arial',
    });
    s.addText(c[1], {
      x: x + 0.3, y: y + 1.1, w: cw - 0.6, h: 1.7,
      fontSize: 16, color: C.white, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.4,
    });
  });
  addFooter(s, 3, TOTAL);
}

// =====================================================================
// 4. 파서 옵션 비교 (Comparison Table)
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('파서 옵션 비교', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });
  s.addText('Knowledge Bases가 지원하는 3가지 파서', {
    x: 0.6, y: 1.0, w: 12, h: 0.4,
    fontSize: 16, color: C.lightSlate, fontFace: 'Arial',
  });

  const headers = ['구분', '기본 파서 (Default)', 'BDA 파서', '파운데이션 모델 파서'];
  const rows = [
    ['지원 형식', '.txt, .md, .html, .docx,\n.xlsx, .pdf (텍스트만)', 'PDF, JPEG, PNG,\n오디오, 비디오', 'PDF, JPEG, PNG,\n구조화 문서'],
    ['멀티모달 처리', '불가', '가능 (이미지·도표·표·\n오디오·비디오)', '가능 (이미지·도표·표)'],
    ['프롬프트\n커스터마이징', '불가', '불가', '가능'],
    ['비용 구조', '무료', '페이지/이미지 수 기준 과금', '입출력 토큰 수 기준 과금'],
    ['파일 크기 제한', '-', '-', '최대 100 GB'],
  ];

  const tblData = [];
  tblData.push(headers.map(h => ({
    text: h,
    options: { bold: true, color: C.white, fill: { color: C.purple }, align: 'center', valign: 'middle', fontSize: 15 },
  })));
  rows.forEach((r, ri) => {
    tblData.push(r.map((c, ci) => ({
      text: c,
      options: {
        color: ci === 0 ? C.orange : C.white,
        bold: ci === 0,
        fill: { color: ri % 2 === 0 ? C.darkNavy : '0D1117' },
        align: ci === 0 ? 'left' : 'center',
        valign: 'middle',
        fontSize: 15,
      },
    })));
  });

  s.addTable(tblData, {
    x: 0.5, y: 1.55, w: 12.3,
    colW: [2.0, 3.3, 3.5, 3.5],
    rowH: 0.65,
    fontFace: 'Arial',
    border: { type: 'solid', color: '0D1117', pt: 1 },
  });

  // 중요 알림
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 5.6, w: 12.3, h: 1.2, rectRadius: 0.08,
    fill: { color: '1A0B3D' }, line: { color: C.orange, width: 1.5 },
  });
  s.addText([
    { text: '⚠ 중요  ', options: { color: C.orange, bold: true, fontSize: 17 } },
    { text: 'BDA 또는 파운데이션 모델 파서를 선택하면 ', options: { color: C.white, fontSize: 16 } },
    { text: '해당 데이터 소스의 모든 PDF', options: { color: C.orange, bold: true, fontSize: 16 } },
    { text: '에 적용됩니다. ', options: { color: C.white, fontSize: 16 } },
    { text: '텍스트만 포함된 PDF도 예외 없이 과금 대상', options: { color: C.orange, bold: true, fontSize: 16 } },
    { text: '이 됩니다.', options: { color: C.white, fontSize: 16 } },
  ], { x: 0.8, y: 5.7, w: 11.7, h: 1.0, valign: 'middle', fontFace: 'Arial', lineSpacingMultiple: 1.3 });

  addFooter(s, 4, TOTAL);
}

// =====================================================================
// 5. BDA 처리 데이터 유형 (4 cards 2x2)
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('BDA가 처리하는 데이터 유형', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });

  const cards = [
    {
      title: '📄  Documents',
      fmts: 'PDF, TIFF, JPEG, PNG, DOCX',
      desc: '텍스트 추출, 도표·차트 설명,\n표 구조 인식, 손글씨 인식\nDOCX는 내부적으로 PDF로 변환',
    },
    {
      title: '🖼  Images',
      fmts: 'JPEG, PNG',
      desc: '이미지 내 텍스트 추출(OCR)\n시각적 설명(visual description) 생성',
    },
    {
      title: '🎙  Audio',
      fmts: 'AMR, FLAC, M4A, MP3, Ogg, WAV',
      desc: '음성 → 텍스트 트랜스크립트\n다국어: EN, DE, ES, FR, IT, PT,\nJA, KO, ZH(대만/광둥)',
    },
    {
      title: '🎬  Video',
      fmts: 'MP4, MOV, AVI, MKV, WEBM\n(H.264 / H.265 / VP8 / VP9)',
      desc: '장면 요약, 텍스트 추출,\n콘텐츠 분류, 오디오 트랜스크립트',
    },
  ];
  const cw = 6.0, ch = 2.75, gap = 0.2;
  cards.forEach((c, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 0.6 + col * (cw + gap);
    const y = 1.4 + row * (ch + 0.2);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w: cw, h: ch, rectRadius: 0.12,
      fill: { color: C.darkNavy }, line: { color: C.magenta, width: 1.2 },
      shadow: mkShadow(),
    });
    s.addText(c.title, {
      x: x + 0.3, y: y + 0.2, w: cw - 0.6, h: 0.5,
      fontSize: 22, bold: true, color: C.orange, fontFace: 'Arial',
    });
    s.addText('지원 형식: ' + c.fmts, {
      x: x + 0.3, y: y + 0.78, w: cw - 0.6, h: 0.65,
      fontSize: 15, color: C.lightSlate, italic: true, fontFace: 'Arial', lineSpacingMultiple: 1.3,
    });
    s.addText(c.desc, {
      x: x + 0.3, y: y + 1.5, w: cw - 0.6, h: 1.2,
      fontSize: 16, color: C.white, fontFace: 'Arial', lineSpacingMultiple: 1.35,
    });
  });
  addFooter(s, 5, TOTAL);
}

// =====================================================================
// 6. 파일 처리 제한 (3 tables)
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('파일 처리 제한 사항', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });

  // Async 표
  s.addText('비동기 (Async) 처리', { x: 0.5, y: 1.2, w: 6.2, h: 0.4, fontSize: 18, bold: true, color: C.orange, fontFace: 'Arial' });
  const async_rows = [
    ['최대 페이지 수 (분할 활성화)', '3,000 페이지'],
    ['최대 파일 크기', '500 MB'],
    ['최대 비디오 길이', '240 분'],
    ['최대 오디오 길이', '240 분'],
    ['최대 이미지 해상도', '8K'],
    ['최소 텍스트 감지 높이', '15 px (8pt @ 150 DPI)'],
  ];
  const asyncTable = [[
    { text: '항목', options: { bold: true, color: C.white, fill: { color: C.purple }, align: 'center', fontSize: 14 } },
    { text: '제한', options: { bold: true, color: C.white, fill: { color: C.purple }, align: 'center', fontSize: 14 } },
  ]];
  async_rows.forEach((r, i) => asyncTable.push(r.map((c, ci) => ({
    text: c, options: { color: ci === 0 ? C.lightSlate : C.orange, bold: ci === 1, fill: { color: i % 2 === 0 ? C.darkNavy : '0D1117' }, fontSize: 14, valign: 'middle', align: ci === 0 ? 'left' : 'center' },
  }))));
  s.addTable(asyncTable, { x: 0.5, y: 1.65, w: 6.2, colW: [3.6, 2.6], rowH: 0.42, fontFace: 'Arial' });

  // Sync 표
  s.addText('동기 (Sync) 처리', { x: 7.0, y: 1.2, w: 6.0, h: 0.4, fontSize: 18, bold: true, color: C.orange, fontFace: 'Arial' });
  const sync_rows = [
    ['최대 페이지 수', '10 페이지'],
    ['최대 파일 크기', '50 MB'],
  ];
  const syncTable = [[
    { text: '항목', options: { bold: true, color: C.white, fill: { color: C.purple }, align: 'center', fontSize: 14 } },
    { text: '제한', options: { bold: true, color: C.white, fill: { color: C.purple }, align: 'center', fontSize: 14 } },
  ]];
  sync_rows.forEach((r, i) => syncTable.push(r.map((c, ci) => ({
    text: c, options: { color: ci === 0 ? C.lightSlate : C.orange, bold: ci === 1, fill: { color: i % 2 === 0 ? C.darkNavy : '0D1117' }, fontSize: 14, valign: 'middle', align: ci === 0 ? 'left' : 'center' },
  }))));
  s.addTable(syncTable, { x: 7.0, y: 1.65, w: 6.0, colW: [3.4, 2.6], rowH: 0.42, fontFace: 'Arial' });

  // 프로젝트 / 블루프린트 제한
  s.addText('프로젝트 / 블루프린트 제한', { x: 7.0, y: 3.4, w: 6.0, h: 0.4, fontSize: 18, bold: true, color: C.orange, fontFace: 'Arial' });
  const bp_rows = [
    ['프로젝트당 최대 블루프린트', '40개'],
    ['계정당 최대 프로젝트', '100개'],
    ['계정당 최대 블루프린트', '1,000개'],
    ['블루프린트 이름 최대 길이', '60자'],
    ['블루프린트 최대 크기', '100,000자 (JSON)'],
  ];
  const bpTable = [[
    { text: '항목', options: { bold: true, color: C.white, fill: { color: C.purple }, align: 'center', fontSize: 14 } },
    { text: '제한', options: { bold: true, color: C.white, fill: { color: C.purple }, align: 'center', fontSize: 14 } },
  ]];
  bp_rows.forEach((r, i) => bpTable.push(r.map((c, ci) => ({
    text: c, options: { color: ci === 0 ? C.lightSlate : C.orange, bold: ci === 1, fill: { color: i % 2 === 0 ? C.darkNavy : '0D1117' }, fontSize: 14, valign: 'middle', align: ci === 0 ? 'left' : 'center' },
  }))));
  s.addTable(bpTable, { x: 7.0, y: 3.85, w: 6.0, colW: [3.4, 2.6], rowH: 0.4, fontFace: 'Arial' });

  // 추가 제약
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 4.65, w: 6.2, h: 2.15, rectRadius: 0.08,
    fill: { color: '1A0B3D' }, line: { color: C.magenta, width: 1.2 },
  });
  s.addText('추가 제약', { x: 0.7, y: 4.75, w: 5.8, h: 0.35, fontSize: 16, bold: true, color: C.orange, fontFace: 'Arial' });
  s.addText([
    { text: '• 수직 방향 텍스트(세로쓰기) 인식 미지원\n', options: { color: C.white, fontSize: 15 } },
    { text: '• 비밀번호 보호 PDF 처리 불가\n', options: { color: C.white, fontSize: 15 } },
    { text: '• PDF 최대: 40인치 / 9,000포인트', options: { color: C.white, fontSize: 15 } },
  ], { x: 0.75, y: 5.15, w: 5.8, h: 1.6, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.5 });

  addFooter(s, 6, TOTAL);
}

// =====================================================================
// 7. 표준 출력 vs 커스텀 출력 (Two Column)
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('BDA 파서 동작 방식', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });
  s.addText('Standard Output / Custom Output / Projects', {
    x: 0.6, y: 1.0, w: 12, h: 0.4, fontSize: 16, color: C.lightSlate, fontFace: 'Arial',
  });

  // 좌: Standard Output
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 1.55, w: 6.2, h: 5.3, rectRadius: 0.12,
    fill: { color: C.darkNavy }, line: { color: C.orange, width: 1.5 }, shadow: mkShadow(),
  });
  s.addText('표준 출력 (Standard Output)', { x: 0.7, y: 1.7, w: 5.8, h: 0.5, fontSize: 20, bold: true, color: C.orange, fontFace: 'Arial' });
  s.addText('블루프린트/프로젝트 없이 파일을 보내면 파일 유형에 맞는 기본 출력을 반환', {
    x: 0.7, y: 2.2, w: 5.8, h: 0.7, fontSize: 14, color: C.lightSlate, italic: true, fontFace: 'Arial', lineSpacingMultiple: 1.3,
  });

  // 작은 표
  const stdRows = [
    ['문서', '텍스트 추출, 문서 요약'],
    ['오디오', '전체 트랜스크립트, 요약'],
    ['비디오', '장면 요약, 감지 텍스트, 분류'],
    ['이미지', '텍스트 추출, 시각적 설명'],
  ];
  const stdTbl = [[
    { text: '데이터 유형', options: { bold: true, color: C.white, fill: { color: C.purple }, fontSize: 14, align: 'center' } },
    { text: '기본 출력', options: { bold: true, color: C.white, fill: { color: C.purple }, fontSize: 14, align: 'center' } },
  ]];
  stdRows.forEach((r, i) => stdTbl.push(r.map((c, ci) => ({
    text: c, options: { color: ci === 0 ? C.orange : C.white, bold: ci === 0, fill: { color: i % 2 === 0 ? '0D1117' : C.darkNavy }, fontSize: 14, valign: 'middle', align: ci === 0 ? 'center' : 'left' },
  }))));
  s.addTable(stdTbl, { x: 0.7, y: 3.05, w: 5.8, colW: [1.6, 4.2], rowH: 0.55, fontFace: 'Arial' });

  s.addText('적용: 모든 데이터 유형(문서/오디오/이미지/비디오)', {
    x: 0.7, y: 6.0, w: 5.8, h: 0.7, fontSize: 14, color: C.lightSlate, fontFace: 'Arial',
  });

  // 우: Custom + Projects
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 6.85, y: 1.55, w: 6.2, h: 2.55, rectRadius: 0.12,
    fill: { color: C.darkNavy }, line: { color: C.magenta, width: 1.5 }, shadow: mkShadow(),
  });
  s.addText('커스텀 출력 (Custom Output)', { x: 7.05, y: 1.7, w: 5.8, h: 0.5, fontSize: 20, bold: true, color: C.magenta, fontFace: 'Arial' });
  s.addText([
    { text: '• ', options: { color: C.orange, fontSize: 15 } },
    { text: '블루프린트(Blueprint)', options: { color: C.orange, bold: true, fontSize: 15 } },
    { text: '로 추출 필드를 정확히 정의\n', options: { color: C.white, fontSize: 15 } },
    { text: '• 문서 / 오디오 / 이미지에 적용 가능\n', options: { color: C.white, fontSize: 15 } },
    { text: '• 비즈니스 워크플로 특화 정보 추출', options: { color: C.white, fontSize: 15 } },
  ], { x: 7.05, y: 2.25, w: 5.8, h: 1.8, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.5 });

  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 6.85, y: 4.25, w: 6.2, h: 2.6, rectRadius: 0.12,
    fill: { color: C.darkNavy }, line: { color: C.purple, width: 1.5 }, shadow: mkShadow(),
  });
  s.addText('프로젝트 (Projects)', { x: 7.05, y: 4.4, w: 5.8, h: 0.5, fontSize: 20, bold: true, color: C.orange, fontFace: 'Arial' });
  s.addText([
    { text: '• 표준/커스텀 출력 구성을 하나의 리소스로 관리\n', options: { color: C.white, fontSize: 15 } },
    { text: '• ', options: { color: C.white, fontSize: 15 } },
    { text: 'InvokeDataAutomationAsync', options: { color: C.orange, fontFace: 'Courier New', fontSize: 14, bold: true } },
    { text: ' API에 ARN 전달\n', options: { color: C.white, fontSize: 15 } },
    { text: '• 스테이지: ', options: { color: C.white, fontSize: 15 } },
    { text: 'LIVE / DEVELOPMENT', options: { color: C.orange, bold: true, fontSize: 15 } },
    { text: '\n• DEVELOPMENT는 콘솔 접근 불가, API 전용', options: { color: C.white, fontSize: 15 } },
  ], { x: 7.05, y: 4.95, w: 5.8, h: 1.85, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.4 });

  addFooter(s, 7, TOTAL);
}

// =====================================================================
// 8. ParsingConfiguration JSON
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('API 구성: ParsingConfiguration', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });
  s.addText('Knowledge Bases 데이터 소스에 BDA 파서를 지정하는 JSON 구조', {
    x: 0.6, y: 1.0, w: 12, h: 0.4, fontSize: 16, color: C.lightSlate, fontFace: 'Arial',
  });

  // 코드 블록
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 1.55, w: 7.5, h: 4.0, rectRadius: 0.08,
    fill: { color: '0D1117' }, line: { color: C.purple, width: 1.2 }, shadow: mkShadow(),
  });
  s.addText('ParsingConfiguration', { x: 0.7, y: 1.65, w: 7, h: 0.4, fontSize: 14, bold: true, color: C.orange, fontFace: 'Courier New' });

  const code = `{
  "parsingStrategy": "BEDROCK_DATA_AUTOMATION",
  "bedrockDataAutomationConfiguration": {
    "parsingModality": "MULTIMODAL"
  }
}`;
  s.addText(code, {
    x: 0.7, y: 2.05, w: 7.1, h: 3.4,
    fontSize: 16, color: C.white, fontFace: 'Courier New', valign: 'top', lineSpacingMultiple: 1.3,
  });

  // 우측 설명
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 8.2, y: 1.55, w: 4.85, h: 4.0, rectRadius: 0.08,
    fill: { color: C.darkNavy }, line: { color: C.magenta, width: 1.2 },
  });
  s.addText('필드 설명', { x: 8.4, y: 1.65, w: 4.5, h: 0.4, fontSize: 18, bold: true, color: C.orange, fontFace: 'Arial' });
  s.addText([
    { text: 'parsingStrategy\n', options: { color: C.orange, bold: true, fontSize: 15, fontFace: 'Courier New' } },
    { text: 'BEDROCK_FOUNDATION_MODEL 또는\nBEDROCK_DATA_AUTOMATION 중 선택\n\n', options: { color: C.white, fontSize: 14 } },
    { text: 'parsingModality\n', options: { color: C.orange, bold: true, fontSize: 15, fontFace: 'Courier New' } },
    { text: 'MULTIMODAL 지정 시 텍스트+이미지\n멀티모달 파싱 활성화', options: { color: C.white, fontSize: 14 } },
  ], { x: 8.4, y: 2.1, w: 4.5, h: 3.4, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.3 });

  // 폴백 메시지
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 5.75, w: 12.55, h: 1.0, rectRadius: 0.08,
    fill: { color: '1A0B3D' }, line: { color: C.orange, width: 1.2 },
  });
  s.addText([
    { text: '🔄 자동 폴백  ', options: { color: C.orange, bold: true, fontSize: 16 } },
    { text: 'BDA 또는 파운데이션 모델 파서가 파일 파싱에 실패하면, ', options: { color: C.white, fontSize: 15 } },
    { text: '자동으로 기본 파서로 폴백', options: { color: C.orange, bold: true, fontSize: 15 } },
    { text: ' 처리됩니다.', options: { color: C.white, fontSize: 15 } },
  ], { x: 0.75, y: 5.85, w: 12.1, h: 0.85, valign: 'middle', fontFace: 'Arial', lineSpacingMultiple: 1.3 });

  addFooter(s, 8, TOTAL);
}

// =====================================================================
// 9. AWS CLI Knowledge Base 생성 예시
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('AWS CLI: Knowledge Base 생성', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 34, bold: true, color: C.white, fontFace: 'Arial',
  });

  s.addText('1. CLI 명령', { x: 0.6, y: 1.05, w: 6, h: 0.4, fontSize: 16, bold: true, color: C.orange, fontFace: 'Arial' });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 1.45, w: 12.55, h: 0.7, rectRadius: 0.06,
    fill: { color: '0D1117' }, line: { color: C.purple, width: 1 },
  });
  s.addText('aws bedrock-agent create-knowledge-base \\\n  --cli-input-json file://kb-bda-parser.json', {
    x: 0.7, y: 1.5, w: 12.2, h: 0.6, fontSize: 14, color: C.white, fontFace: 'Courier New', valign: 'middle', lineSpacingMultiple: 1.2,
  });

  s.addText('2. kb-bda-parser.json (요약 — 플레이스홀더는 실제 값으로 교체)', {
    x: 0.6, y: 2.25, w: 12, h: 0.4, fontSize: 16, bold: true, color: C.orange, fontFace: 'Arial',
  });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 2.65, w: 12.55, h: 4.15, rectRadius: 0.06,
    fill: { color: '0D1117' }, line: { color: C.magenta, width: 1 }, shadow: mkShadow(),
  });

  const kbJson = `{
  "knowledgeBaseConfiguration": {
    "vectorKnowledgeBaseConfiguration": {
      "embeddingModelArn":
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-multimodal-embeddings-v1:0",
      "supplementalDataStorageConfiguration": {
        "storageLocations": [{
          "type": "S3",
          "s3Location": { "uri": "s3://<multimodal-storage-bucket>/" }
        }]
      }
    },
    "type": "VECTOR"
  },
  "storageConfiguration": {
    "opensearchServerlessConfiguration": {
      "collectionArn": "arn:aws:aoss:us-east-1:<account-id>:collection/<collection-id>",
      "vectorIndexName": "<index-name>",
      "fieldMapping": {
        "vectorField": "<vector-field>",
        "textField":   "<text-field>",
        "metadataField": "<metadata-field>"
      }
    },
    "type": "OPENSEARCH_SERVERLESS"
  },
  "name": "<knowledge-base-name>",
  "description": "Knowledge base with BDA parser"
}`;
  s.addText(kbJson, {
    x: 0.75, y: 2.75, w: 12.2, h: 4.0,
    fontSize: 11, color: C.white, fontFace: 'Courier New', valign: 'top', lineSpacingMultiple: 1.15,
  });

  addFooter(s, 9, TOTAL);
}

// =====================================================================
// 10. CRIS - Cross-Region Inference
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('Cross-Region Inference (CRIS) 필수', {
    x: 0.6, y: 0.4, w: 12.5, h: 0.7,
    fontSize: 32, bold: true, color: C.white, fontFace: 'Arial',
  });
  s.addText('BDA는 CRIS를 반드시 사용해야 하며, 추가 비용은 발생하지 않습니다. 데이터는 원본 소스 리전에만 저장됩니다.', {
    x: 0.6, y: 1.05, w: 12.5, h: 0.5, fontSize: 14, color: C.lightSlate, italic: true, fontFace: 'Arial',
  });

  const headers = ['소스 리전', 'ARN 패턴', '처리 가능 리전'];
  const rows = [
    ['US East (N. Virginia)', 'us.data-automation-v1', 'us-east-1/2, us-west-1/2'],
    ['US West (Oregon)', 'us.data-automation-v1', 'us-east-1/2, us-west-1/2'],
    ['Europe (Frankfurt)', 'eu.data-automation-v1', 'eu-central-1, eu-north-1, eu-south-1/2,\neu-west-1/3'],
    ['Europe (Ireland)', 'eu.data-automation-v1', 'eu-central-1, eu-north-1, eu-south-1/2,\neu-west-1/3'],
    ['Europe (London)', 'eu.data-automation-v1', 'eu-west-2'],
    ['Asia Pacific (Mumbai)', 'apac.data-automation-v1', 'ap-northeast-1/2/3, ap-south-1/2,\nap-southeast-1/2/4'],
    ['Asia Pacific (Sydney)', 'apac.data-automation-v1', 'ap-northeast-1/2/3, ap-south-1/2,\nap-southeast-1/2/4'],
    ['AWS GovCloud (US-West)', 'us-gov.data-automation-v1', 'us-gov-west-1'],
  ];
  const tbl = [headers.map(h => ({
    text: h, options: { bold: true, color: C.white, fill: { color: C.purple }, fontSize: 13, align: 'center', valign: 'middle' },
  }))];
  rows.forEach((r, ri) => tbl.push(r.map((c, ci) => ({
    text: c,
    options: {
      color: ci === 0 ? C.orange : C.white,
      bold: ci === 0,
      fill: { color: ri % 2 === 0 ? C.darkNavy : '0D1117' },
      fontSize: 12,
      valign: 'middle',
      align: 'left',
      fontFace: ci === 1 ? 'Courier New' : 'Arial',
    },
  }))));
  s.addTable(tbl, { x: 0.5, y: 1.65, w: 12.55, colW: [3.0, 4.0, 5.55], rowH: 0.5, fontFace: 'Arial' });

  addFooter(s, 10, TOTAL);
}

// =====================================================================
// 11. IAM 정책 코드
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('IAM 권한 구성', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });

  // 좌: BDA 파서 기본 권한
  s.addText('BDA 파서 기본 권한', { x: 0.6, y: 1.05, w: 6, h: 0.4, fontSize: 16, bold: true, color: C.orange, fontFace: 'Arial' });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 1.45, w: 6.3, h: 5.35, rectRadius: 0.08,
    fill: { color: '0D1117' }, line: { color: C.purple, width: 1 }, shadow: mkShadow(),
  });
  const iamBda = `{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeDataAutomationAsync",
    "bedrock:GetDataAutomationStatus"
  ],
  "Resource": [
    "arn:aws:bedrock:us-east-1:<acct>:
       data-automation-profile/
       us.data-automation-v1",
    "arn:aws:bedrock:us-west-2:<acct>:
       data-automation-profile/
       us.data-automation-v1"
  ]
}`;
  s.addText(iamBda, {
    x: 0.7, y: 1.55, w: 6.0, h: 5.15,
    fontSize: 13, color: C.white, fontFace: 'Courier New', valign: 'top', lineSpacingMultiple: 1.25,
  });

  // 우: 멀티모달 S3 권한
  s.addText('멀티모달 스토리지 S3 권한', { x: 7.0, y: 1.05, w: 6, h: 0.4, fontSize: 16, bold: true, color: C.orange, fontFace: 'Arial' });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 6.9, y: 1.45, w: 6.2, h: 3.35, rectRadius: 0.08,
    fill: { color: '0D1117' }, line: { color: C.magenta, width: 1 }, shadow: mkShadow(),
  });
  const iamS3 = `{
  "Effect": "Allow",
  "Action": [
    "s3:GetObject",
    "s3:PutObject",
    "s3:ListBucket"
  ],
  "Resource": [
    "arn:aws:s3:::<multimodal-bucket>",
    "arn:aws:s3:::<multimodal-bucket>/*"
  ]
}`;
  s.addText(iamS3, {
    x: 7.1, y: 1.55, w: 5.95, h: 3.15,
    fontSize: 13, color: C.white, fontFace: 'Courier New', valign: 'top', lineSpacingMultiple: 1.3,
  });

  // 참고: 콘솔 자동 구성
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 6.9, y: 4.95, w: 6.2, h: 1.85, rectRadius: 0.08,
    fill: { color: '1A0B3D' }, line: { color: C.orange, width: 1.2 },
  });
  s.addText('💡 참고', { x: 7.1, y: 5.05, w: 5.8, h: 0.35, fontSize: 14, bold: true, color: C.orange, fontFace: 'Arial' });
  s.addText([
    { text: '• 고객 관리형 KMS 키 사용 시 ', options: { color: C.white, fontSize: 13 } },
    { text: 'KMS 키 작업 + 그랜트 생성 권한', options: { color: C.orange, bold: true, fontSize: 13 } },
    { text: ' 추가 필요\n\n', options: { color: C.white, fontSize: 13 } },
    { text: '• Console에서 KB 생성 시, Bedrock Knowledge Bases가 ', options: { color: C.white, fontSize: 13 } },
    { text: '필요한 권한을 자동 구성', options: { color: C.orange, bold: true, fontSize: 13 } },
  ], { x: 7.1, y: 5.4, w: 5.8, h: 1.4, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.3 });

  addFooter(s, 11, TOTAL);
}

// =====================================================================
// 12. 멀티모달 스토리지 설정
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('멀티모달 스토리지 설정', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });

  // 비교 표
  const headers = ['구분', 'Nova 멀티모달 임베딩', 'BDA 파서'];
  const rows = [
    ['스토리지 설정', '필수', '선택 사항'],
    ['스토리지 미설정 시', '멀티모달 처리 불가', '텍스트 파싱만 가능'],
    ['스토리지 설정 시', '이미지/오디오/비디오 직접 검색', '이미지/오디오/비디오 멀티모달 파싱'],
  ];
  const tbl = [headers.map(h => ({
    text: h, options: { bold: true, color: C.white, fill: { color: C.purple }, fontSize: 15, align: 'center', valign: 'middle' },
  }))];
  rows.forEach((r, ri) => tbl.push(r.map((c, ci) => ({
    text: c,
    options: {
      color: ci === 0 ? C.orange : C.white, bold: ci === 0,
      fill: { color: ri % 2 === 0 ? C.darkNavy : '0D1117' },
      fontSize: 15, valign: 'middle', align: ci === 0 ? 'left' : 'center',
    },
  }))));
  s.addTable(tbl, { x: 0.5, y: 1.2, w: 12.55, colW: [2.55, 5.0, 5.0], rowH: 0.6, fontFace: 'Arial' });

  // 권장 사항 카드 3개
  s.addText('스토리지 구성 권장 사항', {
    x: 0.6, y: 3.5, w: 12, h: 0.4, fontSize: 18, bold: true, color: C.orange, fontFace: 'Arial',
  });

  const tips = [
    {
      t: '✅ 별도 버킷 (권장)',
      d: '데이터 소스 버킷과\n멀티모달 스토리지 버킷을 분리.\n설정 단순 + 충돌 방지 효과적',
      color: C.orange,
    },
    {
      t: '⚠ 동일 버킷 사용 시',
      d: '데이터 소스에 포함 접두사\n(inclusion prefix) 반드시 지정\n— 추출 미디어 재수집 방지',
      color: C.magenta,
    },
    {
      t: '🚫 aws/ 접두사 금지',
      d: '동일 버킷에서 aws/ 접두사는\n사용 불가. 추출 미디어 저장용\n예약 경로입니다',
      color: '#FF6B6B',
    },
  ];
  const cw = 4.0, gap = 0.18;
  tips.forEach((t, i) => {
    const x = 0.5 + i * (cw + gap);
    const y = 3.95;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w: cw, h: 2.0, rectRadius: 0.10,
      fill: { color: C.darkNavy }, line: { color: t.color.replace('#', ''), width: 1.5 },
      shadow: mkShadow(),
    });
    s.addText(t.t, { x: x + 0.25, y: y + 0.15, w: cw - 0.5, h: 0.5, fontSize: 17, bold: true, color: C.orange, fontFace: 'Arial' });
    s.addText(t.d, { x: x + 0.25, y: y + 0.7, w: cw - 0.5, h: 1.25, fontSize: 14, color: C.white, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.35 });
  });

  // S3 라이프사이클 권장
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 6.15, w: 12.55, h: 0.75, rectRadius: 0.08,
    fill: { color: '1A0B3D' }, line: { color: C.purple, width: 1 },
  });
  s.addText([
    { text: '🔄 S3 Lifecycle 정책 권장  ', options: { color: C.orange, bold: true, fontSize: 14 } },
    { text: 'Nova 멀티모달 임베딩은 처리 후 임시 데이터 삭제를 시도 → 임시 경로에 라이프사이클 정책 적용 권장', options: { color: C.white, fontSize: 14 } },
  ], { x: 0.75, y: 6.2, w: 12.1, h: 0.65, valign: 'middle', fontFace: 'Arial' });

  addFooter(s, 12, TOTAL);
}

// =====================================================================
// 13. 임베딩 모델 선택 가이드
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('임베딩 모델 선택 가이드', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });
  s.addText('상황에 맞는 파서 + 임베딩 모델 조합', {
    x: 0.6, y: 1.0, w: 12, h: 0.4, fontSize: 16, color: C.lightSlate, fontFace: 'Arial',
  });

  const headers = ['상황', '권장 구성'];
  const rows = [
    ['텍스트 문서 위주, 멀티모달 불필요', '기본 파서 + 텍스트 임베딩'],
    ['PDF / 이미지 포함, 텍스트 기반 검색', 'BDA 파서 + 텍스트 임베딩'],
    ['이미지 / 오디오 / 비디오 직접 시각 검색', 'BDA 파서 + Nova 멀티모달 임베딩'],
    ['음성 콘텐츠 검색 필요', 'BDA 파서 (Nova 멀티모달은 음성 검색 제한적)'],
    ['이미지 전용 데이터셋 검색', 'Titan Multimodal Embeddings G1 + 기본 파서'],
  ];
  const tbl = [headers.map(h => ({
    text: h, options: { bold: true, color: C.white, fill: { color: C.purple }, fontSize: 16, align: 'center', valign: 'middle' },
  }))];
  rows.forEach((r, ri) => tbl.push(r.map((c, ci) => ({
    text: c,
    options: {
      color: ci === 0 ? C.lightSlate : C.orange, bold: ci === 1,
      fill: { color: ri % 2 === 0 ? C.darkNavy : '0D1117' },
      fontSize: 15, valign: 'middle', align: 'left',
    },
  }))));
  s.addTable(tbl, { x: 0.5, y: 1.55, w: 12.55, colW: [5.5, 7.05], rowH: 0.65, fontFace: 'Arial' });

  // 두 가지 조합 강조 카드
  const combos = [
    {
      title: '텍스트 임베딩 + BDA 파서',
      desc: 'BDA가 멀티모달 → 텍스트 변환 후 저장.\n검색은 텍스트 기반이지만 멀티모달 파싱 결과를 활용',
      color: C.orange,
    },
    {
      title: 'Nova 멀티모달 임베딩 + BDA 파서',
      desc: 'BDA 파싱 후 Nova 임베딩 적용.\nNova는 BDA의 텍스트 변환 결과를 사용 (네이티브 멀티모달 임베딩 X)',
      color: C.magenta,
    },
  ];
  combos.forEach((c, i) => {
    const x = 0.5 + i * (6.27 + 0.1);
    const y = 5.55;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w: 6.27, h: 1.4, rectRadius: 0.10,
      fill: { color: C.darkNavy }, line: { color: c.color, width: 1.2 }, shadow: mkShadow(),
    });
    s.addText(c.title, { x: x + 0.2, y: y + 0.1, w: 6.0, h: 0.35, fontSize: 15, bold: true, color: c.color, fontFace: 'Arial' });
    s.addText(c.desc, { x: x + 0.2, y: y + 0.5, w: 6.0, h: 0.85, fontSize: 13, color: C.white, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.3 });
  });

  addFooter(s, 13, TOTAL);
}

// =====================================================================
// 14. 주요 활용 사례
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('주요 활용 사례', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });

  const cases = [
    { t: '📋 지능형 문서 처리 (IDP)', d: '계약서·청구서·양식에서 구조화된\n데이터 추출 및 분류, 대규모 자동화' },
    { t: '🎬 멀티미디어 분석', d: '영상 장면 요약, 부적절 콘텐츠 감지,\n광고/브랜드 분류, 지능형 영상 검색' },
    { t: '🔍 RAG 강화', d: '문서·이미지·오디오·비디오를\n포함한 지식 베이스로 QA 정확도 향상' },
    { t: '🎙 회의록 / 강의 분석', d: '오디오 및 비디오 파일의\n트랜스크립트 + 요약 자동 생성' },
    { t: '📊 복합 문서 검색', d: 'PDF 내 도표·차트·표·이미지가\n포함된 문서의 시맨틱 검색' },
    { t: '⚙ 단일 API 자동화', d: '모델 오케스트레이션 없이\nInvokeDataAutomationAsync 한 번으로 처리' },
  ];
  const cw = 4.05, ch = 2.5, gx = 0.15, gy = 0.18;
  cases.forEach((c, i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.5 + col * (cw + gx);
    const y = 1.4 + row * (ch + gy);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w: cw, h: ch, rectRadius: 0.10,
      fill: { color: C.darkNavy }, line: { color: C.magenta, width: 1.2 }, shadow: mkShadow(),
    });
    s.addText(c.t, { x: x + 0.25, y: y + 0.2, w: cw - 0.5, h: 0.7, fontSize: 17, bold: true, color: C.orange, fontFace: 'Arial' });
    s.addText(c.d, { x: x + 0.25, y: y + 1.0, w: cw - 0.5, h: 1.4, fontSize: 15, color: C.white, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.4 });
  });
  addFooter(s, 14, TOTAL);
}

// =====================================================================
// 15. 프로젝트 구조 (트리)
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('프로젝트 구조', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });
  s.addText('AWS 인프라 자동화 스크립트 (루트) + Streamlit RAG 애플리케이션 (application/)', {
    x: 0.6, y: 1.0, w: 12.5, h: 0.4, fontSize: 14, color: C.lightSlate, italic: true, fontFace: 'Arial',
  });

  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 1.5, w: 12.55, h: 5.3, rectRadius: 0.08,
    fill: { color: '0D1117' }, line: { color: C.purple, width: 1.2 }, shadow: mkShadow(),
  });

  const tree = `rag-automation/
├── README.md                  # 프로젝트 개요 / BDA·RAG 가이드
├── requirements.txt           # Python 패키지 의존성
├── config.toml                # Streamlit 서버/테마 설정
│
├── installer.py               # AWS 인프라 일괄 배포 (boto3)
├── installer.md               # installer.py 상세 문서
├── uninstaller.py             # 리소스 일괄 삭제
├── add_content.py             # S3 업로드 + KB 동기화
│
└── application/               # Streamlit 챗봇 / RAG / Agent
    ├── app.py                 # Streamlit 진입점 (UI, 모드 선택)
    ├── chat.py                # Bedrock 호출, RAG/이미지 분석 핵심
    ├── info.py                # Bedrock 모델 카탈로그
    ├── langgraph_agent.py     # LangGraph ReAct 에이전트
    ├── mcp_config.py          # MCP 서버 프로파일 로더
    ├── mcp_retrieve.py        # KB retrieve API 래퍼
    ├── mcp_server_retrieve.py # FastMCP retrieve MCP 서버
    ├── utils.py               # 공통 유틸 (설정, 시크릿)
    └── config.json            # 런타임 설정 (region, KB ID 등)`;

  s.addText(tree, {
    x: 0.75, y: 1.65, w: 12.2, h: 5.0,
    fontSize: 13, color: C.white, fontFace: 'Courier New', valign: 'top', lineSpacingMultiple: 1.25,
  });

  addFooter(s, 15, TOTAL);
}

// =====================================================================
// 16. 실행 흐름 (Process Flow 4단계)
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('실행 흐름 요약', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 36, bold: true, color: C.white, fontFace: 'Arial',
  });

  const steps = [
    { n: '1', t: '인프라 프로비저닝', cmd: 'python installer.py', d: 'BDA 파서 적용 KB +\nEC2/ALB/CloudFront 스택 생성\nconfig.json 자동 채움' },
    { n: '2', t: '콘텐츠 적재', cmd: 'python add_content.py', d: '로컬 파일 → S3 업로드\nBDA 기반 인제스션 잡 트리거' },
    { n: '3', t: '애플리케이션 실행', cmd: 'streamlit run app.py', d: 'EC2 User Data가 자동 기동\nCloudFront 도메인으로 외부 접속' },
    { n: '4', t: '질의 처리', cmd: 'app.py → chat.py', d: '단순 LLM / mcp_retrieve RAG /\nlanggraph_agent 분기 처리' },
  ];

  const cw = 3.05, gap = 0.13;
  steps.forEach((step, i) => {
    const x = 0.5 + i * (cw + gap);
    const y = 1.55;
    // Card
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w: cw, h: 4.6, rectRadius: 0.12,
      fill: { color: C.darkNavy }, line: { color: C.orange, width: 1.5 }, shadow: mkShadow(),
    });
    // 번호 원
    s.addShape(pres.shapes.OVAL, {
      x: x + cw / 2 - 0.4, y: y + 0.25, w: 0.8, h: 0.8,
      fill: { color: C.orange }, line: { color: C.orange, width: 0 },
    });
    s.addText(step.n, {
      x: x + cw / 2 - 0.4, y: y + 0.25, w: 0.8, h: 0.8,
      fontSize: 30, bold: true, color: C.white, align: 'center', valign: 'middle', fontFace: 'Arial',
    });
    // 제목
    s.addText(step.t, {
      x: x + 0.15, y: y + 1.2, w: cw - 0.3, h: 0.5,
      fontSize: 18, bold: true, color: C.white, align: 'center', fontFace: 'Arial',
    });
    // 명령
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: x + 0.2, y: y + 1.85, w: cw - 0.4, h: 0.55, rectRadius: 0.06,
      fill: { color: '0D1117' }, line: { color: C.purple, width: 0.8 },
    });
    s.addText(step.cmd, {
      x: x + 0.25, y: y + 1.9, w: cw - 0.5, h: 0.45,
      fontSize: 11, color: C.orange, fontFace: 'Courier New', align: 'center', valign: 'middle',
    });
    // 설명
    s.addText(step.d, {
      x: x + 0.2, y: y + 2.55, w: cw - 0.4, h: 1.95,
      fontSize: 13, color: C.lightSlate, valign: 'top', align: 'left', fontFace: 'Arial', lineSpacingMultiple: 1.4,
    });

    // 화살표
    if (i < steps.length - 1) {
      s.addShape(pres.shapes.RIGHT_TRIANGLE, {
        x: x + cw + 0.0, y: y + 2.05, w: 0.13, h: 0.5,
        fill: { color: C.orange }, line: { color: C.orange, width: 0 },
        rotate: 90,
      });
    }
  });

  // 핵심 포인트
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 6.3, w: 12.55, h: 0.55, rectRadius: 0.08,
    fill: { color: '1A0B3D' }, line: { color: C.magenta, width: 1 },
  });
  s.addText([
    { text: '⏱ 배포 소요 시간 ', options: { color: C.orange, bold: true, fontSize: 13 } },
    { text: '15~25분 (인프라) + CloudFront 활성화 15~20분 추가', options: { color: C.white, fontSize: 13 } },
  ], { x: 0.7, y: 6.32, w: 12.2, h: 0.5, valign: 'middle', fontFace: 'Arial' });

  addFooter(s, 16, TOTAL);
}

// =====================================================================
// 17. 설치 단계 + 문제 해결
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgContent };
  s.addText('설치 단계 & 문제 해결', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 34, bold: true, color: C.white, fontFace: 'Arial',
  });

  // 좌측: 사전 준비 + 4단계 명령
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 1.15, w: 6.2, h: 5.65, rectRadius: 0.10,
    fill: { color: C.darkNavy }, line: { color: C.orange, width: 1.5 }, shadow: mkShadow(),
  });
  s.addText('설치 명령 (4단계)', { x: 0.7, y: 1.25, w: 5.8, h: 0.4, fontSize: 18, bold: true, color: C.orange, fontFace: 'Arial' });

  const cmds = [
    ['1️⃣  Clone & 의존성', 'git clone https://github.com/\n  kyopark2014/rag-automation\ncd rag-automation\npip install -r requirements.txt'],
    ['2️⃣  AWS 자격증명', 'aws configure\n# 또는\naws sso login --profile <p>\nexport AWS_PROFILE=<p>'],
    ['3️⃣  인프라 배포', 'python installer.py\n# 약 15~25분 소요\n# config.json 자동 생성'],
    ['4️⃣  로컬 실행 / 정리', 'streamlit run application/app.py\n# 정리:\npython uninstaller.py --yes'],
  ];
  cmds.forEach((c, i) => {
    const y = 1.7 + i * 1.27;
    s.addText(c[0], { x: 0.7, y, w: 5.8, h: 0.32, fontSize: 13, bold: true, color: C.white, fontFace: 'Arial' });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 0.7, y: y + 0.32, w: 5.8, h: 0.88, rectRadius: 0.05,
      fill: { color: '0D1117' }, line: { color: C.purple, width: 0.8 },
    });
    s.addText(c[1], { x: 0.8, y: y + 0.34, w: 5.6, h: 0.85, fontSize: 10, color: C.lightSlate, fontFace: 'Courier New', valign: 'top', lineSpacingMultiple: 1.15 });
  });

  // 우측: 사전 준비 표
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 6.85, y: 1.15, w: 6.2, h: 2.5, rectRadius: 0.10,
    fill: { color: C.darkNavy }, line: { color: C.magenta, width: 1.5 }, shadow: mkShadow(),
  });
  s.addText('사전 준비 (Prerequisites)', { x: 7.05, y: 1.25, w: 5.8, h: 0.4, fontSize: 16, bold: true, color: C.orange, fontFace: 'Arial' });
  s.addText([
    { text: '• AWS 계정  ', options: { color: C.orange, bold: true, fontSize: 13 } },
    { text: '관리자 / 인프라 생성 권한\n', options: { color: C.white, fontSize: 13 } },
    { text: '• 리전  ', options: { color: C.orange, bold: true, fontSize: 13 } },
    { text: 'us-west-2 (BDA / Nova / Claude 가능)\n', options: { color: C.white, fontSize: 13 } },
    { text: '• Bedrock 모델 액세스  ', options: { color: C.orange, bold: true, fontSize: 13 } },
    { text: 'Console에서 활성화\n', options: { color: C.white, fontSize: 13 } },
    { text: '• Python  ', options: { color: C.orange, bold: true, fontSize: 13 } },
    { text: '3.10 이상\n', options: { color: C.white, fontSize: 13 } },
    { text: '• AWS CLI  ', options: { color: C.orange, bold: true, fontSize: 13 } },
    { text: '자격증명 설정 완료', options: { color: C.white, fontSize: 13 } },
  ], { x: 7.05, y: 1.7, w: 5.85, h: 1.85, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.4 });

  // 우측 하단: Troubleshooting
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 6.85, y: 3.8, w: 6.2, h: 3.0, rectRadius: 0.10,
    fill: { color: C.darkNavy }, line: { color: C.magenta, width: 1.5 }, shadow: mkShadow(),
  });
  s.addText('🛠 문제 해결 (Troubleshooting)', { x: 7.05, y: 3.9, w: 5.8, h: 0.4, fontSize: 16, bold: true, color: C.orange, fontFace: 'Arial' });
  s.addText([
    { text: '• AccessDenied (Bedrock)  ', options: { color: C.orange, bold: true, fontSize: 12 } },
    { text: 'Model access 활성화 + InvokeModel/Async 권한 확인\n', options: { color: C.white, fontSize: 12 } },
    { text: '• ResourceNotFound (KB)  ', options: { color: C.orange, bold: true, fontSize: 12 } },
    { text: 'config.json의 KB ID 일치 확인 (자동 복구 시도)\n', options: { color: C.white, fontSize: 12 } },
    { text: '• CloudFront 502/503  ', options: { color: C.orange, bold: true, fontSize: 12 } },
    { text: '15~20분 활성화 대기, ALB 타겟 헬스(:8501) 확인\n', options: { color: C.white, fontSize: 12 } },
    { text: '• add_content.py 설정 로드 실패  ', options: { color: C.orange, bold: true, fontSize: 12 } },
    { text: 'installer.py 정상 완료 / config.json 생성 확인\n', options: { color: C.white, fontSize: 12 } },
    { text: '• BDA 인제스션 실패  ', options: { color: C.orange, bold: true, fontSize: 12 } },
    { text: '500MB / 3,000페이지 초과·암호 PDF 여부 확인', options: { color: C.white, fontSize: 12 } },
  ], { x: 7.05, y: 4.3, w: 5.85, h: 2.45, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.4 });

  addFooter(s, 17, TOTAL);
}

// =====================================================================
// 18. 참고 문서 / Thank You
// =====================================================================
{
  const s = pres.addSlide();
  s.background = { data: bgTitle };

  s.addText('참고 문서 & 마무리', {
    x: 0.6, y: 0.4, w: 12, h: 0.7,
    fontSize: 32, bold: true, color: C.white, fontFace: 'Arial',
  });

  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.6, y: 1.15, w: 0.9, h: 0.05, fill: { color: C.orange }, line: { color: C.orange, width: 0 },
  });

  // 핵심 메시지
  s.addText([
    { text: 'Bedrock Data Automation', options: { color: C.orange, bold: true, fontSize: 22 } },
    { text: '으로 멀티모달 RAG를 ', options: { color: C.white, fontSize: 22 } },
    { text: '단일 API', options: { color: C.orange, bold: true, fontSize: 22 } },
    { text: '로,\n', options: { color: C.white, fontSize: 22 } },
    { text: 'installer.py', options: { color: C.orange, bold: true, fontSize: 22, fontFace: 'Courier New' } },
    { text: '로 인프라를 ', options: { color: C.white, fontSize: 22 } },
    { text: '15분', options: { color: C.orange, bold: true, fontSize: 22 } },
    { text: '만에 자동 배포', options: { color: C.white, fontSize: 22 } },
  ], { x: 0.6, y: 1.4, w: 12, h: 1.3, valign: 'top', fontFace: 'Arial', lineSpacingMultiple: 1.4 });

  // 참고 링크 표
  s.addText('📚 AWS 공식 문서', {
    x: 0.6, y: 2.95, w: 12, h: 0.4, fontSize: 17, bold: true, color: C.orange, fontFace: 'Arial',
  });
  const links = [
    ['Parsing options for your data source', 'docs.aws.amazon.com/.../kb-advanced-parsing.html'],
    ['What is Bedrock Data Automation', 'docs.aws.amazon.com/.../bda.html'],
    ['How Bedrock Data Automation works', 'docs.aws.amazon.com/.../bda-how-it-works.html'],
    ['Cross Region support for BDA', 'docs.aws.amazon.com/.../bda-cris.html'],
    ['Standard output in BDA', 'docs.aws.amazon.com/.../bda-standard-output.html'],
    ['Bedrock Data Automation projects', 'docs.aws.amazon.com/.../bda-projects.html'],
    ['Create KB for multimodal content', 'docs.aws.amazon.com/.../kb-multimodal-create.html'],
  ];
  const tbl = links.map((l, i) => l.map((c, ci) => ({
    text: c,
    options: {
      color: ci === 0 ? C.white : C.lightSlate, bold: ci === 0,
      fill: { color: i % 2 === 0 ? C.darkNavy : '0D1117' },
      fontSize: 12, valign: 'middle', align: 'left',
      fontFace: ci === 1 ? 'Courier New' : 'Arial',
    },
  })));
  s.addTable(tbl, { x: 0.6, y: 3.4, w: 12.2, colW: [5.5, 6.7], rowH: 0.36, fontFace: 'Arial' });

  // Thank you
  s.addText('Thank You', {
    x: 0.6, y: 6.15, w: 6, h: 0.6, fontSize: 36, bold: true, color: C.orange, fontFace: 'Arial',
  });
  s.addText('감사합니다', {
    x: 0.6, y: 6.65, w: 6, h: 0.4, fontSize: 18, color: C.white, fontFace: 'Arial',
  });
  s.addText([
    { text: 'GitHub  ', options: { color: C.orange, bold: true, fontSize: 13 } },
    { text: 'github.com/kyopark2014/rag-automation', options: { color: C.white, fontSize: 13, fontFace: 'Courier New' } },
  ], { x: 7.0, y: 6.55, w: 6.0, h: 0.4, fontFace: 'Arial', align: 'right', valign: 'middle' });
  s.addText('발표자  ·  Solutions Architect  ·  AWS', {
    x: 7.0, y: 6.85, w: 6.0, h: 0.3, fontSize: 11, color: C.lightSlate, align: 'right', fontFace: 'Arial',
  });
}

pres.writeFile({ fileName: OUT }).then(fn => {
  console.log('OK:', fn);
}).catch(err => {
  console.error('ERROR:', err);
  process.exit(1);
});
