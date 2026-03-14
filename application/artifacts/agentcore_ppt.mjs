import pptxgen from 'pptxgenjs';

const prs = new pptxgen();
prs.layout = 'LAYOUT_WIDE'; // 16:9

// ─── Color Palette (Ocean Gradient + AWS Orange accent) ───────────────────────
const C = {
  dark:    '0A1628',   // deep navy (title/conclusion bg)
  primary: '065A82',   // deep blue
  teal:    '1C7293',   // teal
  accent:  'FF9900',   // AWS orange
  light:   'E8F4FD',   // very light blue (content bg)
  white:   'FFFFFF',
  gray:    '6B7A8D',
  lightGray: 'D0DCE8',
};

// ─── Shared helpers ────────────────────────────────────────────────────────────
function addBadge(slide, text, x, y, w = 1.6, color = C.accent) {
  slide.addShape(prs.ShapeType.roundRect, {
    x, y, w, h: 0.32,
    fill: { color },
    line: { color, width: 0 },
    rectRadius: 0.06,
  });
  slide.addText(text, {
    x, y, w, h: 0.32,
    fontSize: 9, bold: true, color: C.dark,
    align: 'center', valign: 'middle',
  });
}

function addCard(slide, x, y, w, h, fillColor = C.white, lineColor = C.lightGray) {
  slide.addShape(prs.ShapeType.roundRect, {
    x, y, w, h,
    fill: { color: fillColor },
    line: { color: lineColor, width: 1 },
    rectRadius: 0.12,
    shadow: { type: 'outer', color: '000000', opacity: 0.08, blur: 6, offset: 2, angle: 90 },
  });
}

function addIconCircle(slide, emoji, x, y, size = 0.55, bg = C.primary) {
  slide.addShape(prs.ShapeType.ellipse, {
    x, y, w: size, h: size,
    fill: { color: bg },
    line: { color: bg, width: 0 },
  });
  slide.addText(emoji, {
    x, y, w: size, h: size,
    fontSize: size * 18, align: 'center', valign: 'middle',
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// SLIDE 1 — Title
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = prs.addSlide();
  // Full dark background
  s.addShape(prs.ShapeType.rect, { x: 0, y: 0, w: '100%', h: '100%', fill: { color: C.dark } });

  // Left accent bar
  s.addShape(prs.ShapeType.rect, { x: 0, y: 0, w: 0.08, h: '100%', fill: { color: C.accent } });

  // Decorative circles (top-right)
  s.addShape(prs.ShapeType.ellipse, { x: 8.5, y: -0.8, w: 3.2, h: 3.2, fill: { color: C.primary }, line: { color: C.primary } });
  s.addShape(prs.ShapeType.ellipse, { x: 9.2, y: -0.3, w: 2.0, h: 2.0, fill: { color: C.teal }, line: { color: C.teal } });

  // AWS badge
  addBadge(s, '🟠  AWS Bedrock', 0.6, 0.55, 1.9, C.accent);

  // Main title
  s.addText('Amazon Bedrock\nAgentCore', {
    x: 0.6, y: 1.1, w: 7.5, h: 2.0,
    fontSize: 52, bold: true, color: C.white,
    fontFace: 'Calibri',
    lineSpacingMultiple: 1.15,
  });

  // Subtitle
  s.addText('AI 에이전트를 안전하게 배포하고 운영하는\n엔터프라이즈급 완전관리형 플랫폼', {
    x: 0.6, y: 3.2, w: 7.5, h: 1.0,
    fontSize: 18, color: C.lightGray,
    fontFace: 'Calibri',
    lineSpacingMultiple: 1.4,
  });

  // Bottom info bar
  s.addShape(prs.ShapeType.rect, { x: 0, y: 6.8, w: '100%', h: 0.7, fill: { color: C.primary } });
  s.addText('Preview 출시: 2025년 7월 16일   |   GA: 2025년 12월 (re:Invent 2025)', {
    x: 0.6, y: 6.82, w: 12, h: 0.5,
    fontSize: 12, color: C.lightGray, fontFace: 'Calibri',
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// SLIDE 2 — What is AgentCore?
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = prs.addSlide();
  s.addShape(prs.ShapeType.rect, { x: 0, y: 0, w: '100%', h: '100%', fill: { color: C.light } });

  // Top header band
  s.addShape(prs.ShapeType.rect, { x: 0, y: 0, w: '100%', h: 1.1, fill: { color: C.primary } });
  s.addText('AgentCore란 무엇인가?', {
    x: 0.5, y: 0.15, w: 11, h: 0.8,
    fontSize: 32, bold: true, color: C.white, fontFace: 'Calibri',
  });

  // Left column — definition card
  addCard(s, 0.4, 1.3, 5.6, 4.8);
  s.addText('🤖', { x: 0.55, y: 1.5, w: 0.7, h: 0.7, fontSize: 28 });
  s.addText('정의', { x: 1.35, y: 1.55, w: 4.5, h: 0.5, fontSize: 18, bold: true, color: C.primary, fontFace: 'Calibri' });
  s.addText(
    'Amazon Bedrock AgentCore는 AWS가 2025년 7월에 출시한 완전관리형(Fully Managed) 서비스로, 개발자가 AI 에이전트를 빠르고 안전하게 배포·운영할 수 있도록 설계된 엔터프라이즈급 플랫폼입니다.\n\n어떤 프레임워크(LangChain, LangGraph, CrewAI, Strands 등)와 어떤 모델(Amazon Bedrock 또는 외부 모델)도 지원하며, 인프라 복잡성을 제거해 개발자가 혁신에 집중할 수 있게 합니다.',
    { x: 0.55, y: 2.15, w: 5.3, h: 3.7, fontSize: 13.5, color: C.dark, fontFace: 'Calibri', lineSpacingMultiple: 1.45 }
  );

  // Right column — key facts
  const facts = [
    { icon: '📅', label: 'Preview 출시', val: '2025년 7월 16일' },
    { icon: '🚀', label: 'GA 출시', val: '2025년 12월 (re:Invent)' },
    { icon: '🔧', label: '지원 프레임워크', val: 'LangChain, LangGraph, CrewAI, Strands, OpenAI SDK 등' },
    { icon: '☁️', label: '인프라', val: '완전관리형 서버리스' },
  ];
  facts.forEach((f, i) => {
    const fy = 1.3 + i * 1.18;
    addCard(s, 6.3, fy, 6.0, 1.0);
    s.addText(f.icon, { x: 6.45, y: fy + 0.15, w: 0.6, h: 0.6, fontSize: 22 });
    s.addText(f.label, { x: 7.15, y: fy + 0.1, w: 5.0, h: 0.3, fontSize: 11, bold: true, color: C.gray, fontFace: 'Calibri' });
    s.addText(f.val, { x: 7.15, y: fy + 0.42, w: 5.0, h: 0.45, fontSize: 13.5, bold: true, color: C.primary, fontFace: 'Calibri' });
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// SLIDE 3 — 7 Core Components (icon grid)
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = prs.addSlide();
  s.addShape(prs.ShapeType.rect, { x: 0, y: 0, w: '100%', h: '100%', fill: { color: C.dark } });

  s.addText('7가지 핵심 컴포넌트', {
    x: 0.5, y: 0.25, w: 12, h: 0.7,
    fontSize: 34, bold: true, color: C.white, fontFace: 'Calibri', align: 'center',
  });
  s.addText('AgentCore를 구성하는 모듈형 빌딩 블록', {
    x: 0.5, y: 0.9, w: 12, h: 0.4,
    fontSize: 14, color: C.lightGray, fontFace: 'Calibri', align: 'center',
  });

  const components = [
    { icon: '⚡', name: 'Runtime', desc: '서버리스 실행 환경\n세션 격리 · 8시간 실행\nA2A 프로토콜 지원', color: '1A6B9A' },
    { icon: '🧠', name: 'Memory', desc: '세션 · 장기 메모리\n에피소딕 메모리\n과거 경험 학습', color: '0E7C6B' },
    { icon: '🔭', name: 'Observability', desc: 'CloudWatch 통합\n실행 추적 · 디버깅\n커스텀 스코어링', color: '7B4F9E' },
    { icon: '🔐', name: 'Identity', desc: '에이전트 인증/인가\nOAuth 2.0 지원\nOkta · Entra ID 연동', color: 'B85A00' },
    { icon: '🌐', name: 'Gateway', desc: 'API → MCP 도구 변환\nOpenAPI · Lambda 지원\n통합 인증 관리', color: '1A6B9A' },
    { icon: '🖥️', name: 'Browser', desc: '클라우드 브라우저\n웹 자동화 스케일링\nWeb Bot Auth 지원', color: '0E7C6B' },
    { icon: '💻', name: 'Code Interpreter', desc: '격리된 코드 실행\n에이전트 생성 코드\n안전한 샌드박스', color: '7B4F9E' },
  ];

  // 4 + 3 grid layout
  const positions = [
    [0.3, 1.55], [3.05, 1.55], [5.8, 1.55], [8.55, 1.55],
    [1.65, 4.05], [4.4, 4.05], [7.15, 4.05],
  ];

  components.forEach((c, i) => {
    const [cx, cy] = positions[i];
    addCard(s, cx, cy, 2.55, 2.3, '1A2744', '2A3F6A');
    // top color bar
    s.addShape(prs.ShapeType.rect, { x: cx, y: cy, w: 2.55, h: 0.06, fill: { color: c.color } });
    addIconCircle(s, c.icon, cx + 0.95, cy + 0.12, 0.6, c.color);
    s.addText(c.name, {
      x: cx + 0.1, y: cy + 0.78, w: 2.35, h: 0.38,
      fontSize: 14, bold: true, color: C.white, fontFace: 'Calibri', align: 'center',
    });
    s.addText(c.desc, {
      x: cx + 0.1, y: cy + 1.18, w: 2.35, h: 1.0,
      fontSize: 10.5, color: C.lightGray, fontFace: 'Calibri', align: 'center',
      lineSpacingMultiple: 1.35,
    });
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// SLIDE 4 — Key Features Deep Dive (2-column)
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = prs.addSlide();
  s.addShape(prs.ShapeType.rect, { x: 0, y: 0, w: '100%', h: '100%', fill: { color: C.light } });
  s.addShape(prs.ShapeType.rect, { x: 0, y: 0, w: '100%', h: 1.1, fill: { color: C.teal } });
  s.addText('주요 기능 심층 분석', {
    x: 0.5, y: 0.18, w: 11, h: 0.75,
    fontSize: 32, bold: true, color: C.white, fontFace: 'Calibri',
  });

  const features = [
    {
      icon: '🔄', title: 'A2A 프로토콜 지원',
      body: 'Agent-to-Agent(A2A) 프로토콜로 서로 다른 프레임워크(Strands, LangGraph, OpenAI SDK 등)로 만든 에이전트들이 컨텍스트와 추론을 공유하며 협업 가능',
    },
    {
      icon: '🛡️', title: 'Policy & Guardrails',
      body: 'CEDAR 언어 기반 세밀한 정책 제어로 에이전트가 실행 전 허용된 작업만 수행하도록 강제. 13개 사전 빌드 평가기로 정확성·안전성·유용성 지속 모니터링',
    },
    {
      icon: '🧩', title: 'MCP 통합 Gateway',
      body: '기존 REST API, Lambda 함수를 Model Context Protocol(MCP) 호환 도구로 변환. OpenAPI, Smithy, Lambda 입력 지원. 인그레스/이그레스 인증 통합 관리',
    },
    {
      icon: '🔒', title: '엔터프라이즈 보안',
      body: 'VPC, AWS PrivateLink, CloudFormation, 리소스 태깅 지원. Okta·Microsoft Entra ID·Amazon Cognito 등 기업 IdP와 완벽 통합. OAuth 2.0 기반 M2M 인증',
    },
    {
      icon: '📊', title: '에피소딕 메모리',
      body: '에이전트가 과거 경험에서 학습하고 시간이 지남에 따라 지식을 축적. 다주(multi-week) 워크플로우에서 이전 작업 컨텍스트를 기억해 신뢰할 수 있는 워커로 진화',
    },
    {
      icon: '🎙️', title: '양방향 스트리밍',
      body: '에이전트가 동시에 듣고 응답하며 대화 중 인터럽션과 컨텍스트 변화를 처리. 자연스러운 음성 에이전트 구현을 위한 핵심 기능 (2025년 12월 추가)',
    },
  ];

  const cols = [[0.3, 6.55], [6.75, 6.55]];
  features.forEach((f, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const fx = cols[col][0];
    const fy = 1.3 + row * 1.85;
    addCard(s, fx, fy, 6.2, 1.7);
    addIconCircle(s, f.icon, fx + 0.15, fy + 0.55, 0.52, C.teal);
    s.addText(f.title, {
      x: fx + 0.82, y: fy + 0.12, w: 5.2, h: 0.38,
      fontSize: 14, bold: true, color: C.primary, fontFace: 'Calibri',
    });
    s.addText(f.body, {
      x: fx + 0.82, y: fy + 0.52, w: 5.2, h: 1.1,
      fontSize: 11.5, color: C.dark, fontFace: 'Calibri', lineSpacingMultiple: 1.35,
    });
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// SLIDE 5 — Architecture & Use Cases
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = prs.addSlide();
  s.addShape(prs.ShapeType.rect, { x: 0, y: 0, w: '100%', h: '100%', fill: { color: C.light } });
  s.addShape(prs.ShapeType.rect, { x: 0, y: 0, w: '100%', h: 1.1, fill: { color: C.primary } });
  s.addText('아키텍처 & 활용 사례', {
    x: 0.5, y: 0.18, w: 11, h: 0.75,
    fontSize: 32, bold: true, color: C.white, fontFace: 'Calibri',
  });

  // Architecture flow (left side)
  addCard(s, 0.3, 1.25, 6.0, 5.3);
  s.addText('🏗️  AgentCore 아키텍처 흐름', {
    x: 0.5, y: 1.4, w: 5.6, h: 0.45,
    fontSize: 15, bold: true, color: C.primary, fontFace: 'Calibri',
  });

  const flow = [
    { n: '1', label: '사용자 / 시스템 요청', sub: 'Customer Interface' },
    { n: '2', label: 'AgentCore Runtime', sub: '서버리스 실행 · 세션 격리' },
    { n: '3', label: 'AgentCore Gateway', sub: 'MCP 도구 라우팅' },
    { n: '4', label: 'AgentCore Memory', sub: '컨텍스트 저장 · 에피소딕 학습' },
    { n: '5', label: 'AgentCore Identity', sub: '인증 · 권한 검증' },
    { n: '6', label: 'AgentCore Observability', sub: 'CloudWatch 추적 · 모니터링' },
  ];

  flow.forEach((f, i) => {
    const fy = 2.0 + i * 0.75;
    // number circle
    s.addShape(prs.ShapeType.ellipse, { x: 0.5, y: fy, w: 0.42, h: 0.42, fill: { color: C.accent }, line: { color: C.accent } });
    s.addText(f.n, { x: 0.5, y: fy, w: 0.42, h: 0.42, fontSize: 13, bold: true, color: C.dark, align: 'center', valign: 'middle' });
    s.addText(f.label, { x: 1.05, y: fy, w: 4.0, h: 0.28, fontSize: 13, bold: true, color: C.dark, fontFace: 'Calibri' });
    s.addText(f.sub, { x: 1.05, y: fy + 0.26, w: 4.0, h: 0.22, fontSize: 10.5, color: C.gray, fontFace: 'Calibri' });
    // connector arrow
    if (i < flow.length - 1) {
      s.addShape(prs.ShapeType.line, { x: 0.71, y: fy + 0.44, w: 0, h: 0.28, line: { color: C.lightGray, width: 1.5 } });
    }
  });

  // Use cases (right side)
  const usecases = [
    { icon: '🎧', title: '고객 지원 자동화', desc: '24/7 지능형 고객 응대, 티켓 자동 분류 및 해결' },
    { icon: '🔧', title: 'SRE / DevOps 에이전트', desc: '인시던트 감지·분석·복구 자동화, 멀티 에이전트 협업' },
    { icon: '📋', title: '엔터프라이즈 워크플로우', desc: '조달·승인·보고 등 복잡한 비즈니스 프로세스 자동화' },
    { icon: '🔬', title: '코드 생성 & 실행', desc: '에이전트가 코드를 생성하고 격리 환경에서 안전하게 실행' },
  ];

  usecases.forEach((u, i) => {
    const uy = 1.25 + i * 1.32;
    addCard(s, 6.55, uy, 5.7, 1.18);
    addIconCircle(s, u.icon, 6.7, uy + 0.3, 0.5, C.primary);
    s.addText(u.title, { x: 7.35, y: uy + 0.1, w: 4.7, h: 0.35, fontSize: 13.5, bold: true, color: C.primary, fontFace: 'Calibri' });
    s.addText(u.desc, { x: 7.35, y: uy + 0.48, w: 4.7, h: 0.6, fontSize: 11.5, color: C.dark, fontFace: 'Calibri', lineSpacingMultiple: 1.3 });
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// SLIDE 6 — Why AgentCore? (Before vs After)
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = prs.addSlide();
  s.addShape(prs.ShapeType.rect, { x: 0, y: 0, w: '100%', h: '100%', fill: { color: C.light } });
  s.addShape(prs.ShapeType.rect, { x: 0, y: 0, w: '100%', h: 1.1, fill: { color: C.teal } });
  s.addText('왜 AgentCore인가? — Before vs After', {
    x: 0.5, y: 0.18, w: 11, h: 0.75,
    fontSize: 30, bold: true, color: C.white, fontFace: 'Calibri',
  });

  // Before column
  addCard(s, 0.3, 1.25, 5.8, 5.3, 'FFF0F0', 'FFBBBB');
  s.addShape(prs.ShapeType.rect, { x: 0.3, y: 1.25, w: 5.8, h: 0.5, fill: { color: 'E05252' }, line: { color: 'E05252' } });
  s.addText('❌  AgentCore 이전', { x: 0.4, y: 1.3, w: 5.6, h: 0.4, fontSize: 16, bold: true, color: C.white, fontFace: 'Calibri' });

  const befores = [
    '인프라 직접 구축 및 관리 필요',
    '프레임워크별 별도 통합 작업',
    '보안·인증 로직 직접 구현',
    '에이전트 상태 관리 복잡',
    '모니터링·디버깅 도구 부재',
    '멀티 에이전트 협업 구현 어려움',
    '프로덕션 배포까지 수개월 소요',
  ];
  befores.forEach((b, i) => {
    s.addText(`• ${b}`, {
      x: 0.55, y: 1.9 + i * 0.6, w: 5.4, h: 0.5,
      fontSize: 13, color: '8B0000', fontFace: 'Calibri',
    });
  });

  // After column
  addCard(s, 6.3, 1.25, 5.8, 5.3, 'F0FFF4', 'AADDBB');
  s.addShape(prs.ShapeType.rect, { x: 6.3, y: 1.25, w: 5.8, h: 0.5, fill: { color: '2E8B57' }, line: { color: '2E8B57' } });
  s.addText('✅  AgentCore 이후', { x: 6.4, y: 1.3, w: 5.6, h: 0.4, fontSize: 16, bold: true, color: C.white, fontFace: 'Calibri' });

  const afters = [
    '완전관리형 서버리스 환경 제공',
    '모든 프레임워크 & 모델 지원',
    '내장 인증·인가·정책 제어',
    '세션 격리 + 에피소딕 메모리',
    'CloudWatch 통합 실시간 관찰',
    'A2A 프로토콜로 에이전트 협업',
    '몇 분 만에 프로덕션 배포 가능',
  ];
  afters.forEach((a, i) => {
    s.addText(`• ${a}`, {
      x: 6.45, y: 1.9 + i * 0.6, w: 5.4, h: 0.5,
      fontSize: 13, color: '1A5C35', fontFace: 'Calibri',
    });
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// SLIDE 7 — Conclusion
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = prs.addSlide();
  s.addShape(prs.ShapeType.rect, { x: 0, y: 0, w: '100%', h: '100%', fill: { color: C.dark } });

  // Decorative circles
  s.addShape(prs.ShapeType.ellipse, { x: -1.0, y: 4.5, w: 4.0, h: 4.0, fill: { color: C.primary }, line: { color: C.primary } });
  s.addShape(prs.ShapeType.ellipse, { x: 10.0, y: -1.0, w: 3.5, h: 3.5, fill: { color: C.teal }, line: { color: C.teal } });

  s.addText('핵심 요약', {
    x: 0.6, y: 0.4, w: 11, h: 0.6,
    fontSize: 16, color: C.accent, bold: true, fontFace: 'Calibri',
  });
  s.addText('Amazon Bedrock AgentCore', {
    x: 0.6, y: 0.95, w: 11, h: 0.9,
    fontSize: 42, bold: true, color: C.white, fontFace: 'Calibri',
  });
  s.addText('AI 에이전트의 프로덕션 시대를 여는 플랫폼', {
    x: 0.6, y: 1.85, w: 11, h: 0.55,
    fontSize: 20, color: C.lightGray, fontFace: 'Calibri',
  });

  const summaries = [
    { icon: '🔧', text: '7가지 모듈형 컴포넌트로 에이전트 개발의 모든 단계 커버' },
    { icon: '🌐', text: '어떤 프레임워크·모델도 지원하는 오픈 생태계' },
    { icon: '🛡️', text: 'CEDAR 정책 + 13개 평가기로 엔터프라이즈 신뢰성 확보' },
    { icon: '🚀', text: 'A2A 프로토콜로 멀티 에이전트 협업의 새로운 표준 제시' },
  ];

  summaries.forEach((sm, i) => {
    const sy = 2.65 + i * 0.88;
    addIconCircle(s, sm.icon, 0.6, sy + 0.12, 0.5, C.accent);
    s.addText(sm.text, {
      x: 1.25, y: sy + 0.08, w: 10.5, h: 0.55,
      fontSize: 15, color: C.white, fontFace: 'Calibri',
    });
  });

  // Bottom bar
  s.addShape(prs.ShapeType.rect, { x: 0, y: 6.8, w: '100%', h: 0.7, fill: { color: C.accent } });
  s.addText('🔗  aws.amazon.com/bedrock/agentcore   |   2025 AWS re:Invent 발표 기준', {
    x: 0.5, y: 6.85, w: 12, h: 0.5,
    fontSize: 12, bold: true, color: C.dark, fontFace: 'Calibri', align: 'center',
  });
}

// ─── Save ──────────────────────────────────────────────────────────────────────
await prs.writeFile({ fileName: '/Users/ksdyb/Documents/src/agent-skills/application/artifacts/agentcore.pptx' });
console.log('✅ PPT saved: artifacts/agentcore.pptx');
