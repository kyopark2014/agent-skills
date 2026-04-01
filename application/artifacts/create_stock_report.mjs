import {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  ImageRun, Header, Footer, AlignmentType, HeadingLevel, BorderStyle,
  WidthType, ShadingType, VerticalAlign, PageNumber, LevelFormat,
  TableOfContents, PageBreak
} from 'docx';
import fs from 'fs';
import path from 'path';

const ARTIFACTS = '/Users/ksdyb/Documents/src/agent-skills/application/artifacts';

// ── 공통 스타일 ──────────────────────────────────────────────────────
const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const headerBorder = { style: BorderStyle.SINGLE, size: 1, color: "2E75B6" };
const headerBorders = { top: headerBorder, bottom: headerBorder, left: headerBorder, right: headerBorder };

function cell(text, opts = {}) {
  const { bold = false, align = AlignmentType.LEFT, shade = null, width = 2340, color = "000000" } = opts;
  return new TableCell({
    borders: shade ? headerBorders : borders,
    width: { size: width, type: WidthType.DXA },
    shading: shade ? { fill: shade, type: ShadingType.CLEAR } : undefined,
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({
      alignment: align,
      children: [new TextRun({ text, bold, font: "Arial", size: 20, color })]
    })]
  });
}

function heading1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 360, after: 180 },
    children: [new TextRun({ text, bold: true, font: "Arial", size: 32, color: "1F3864" })]
  });
}

function heading2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 240, after: 120 },
    children: [new TextRun({ text, bold: true, font: "Arial", size: 26, color: "2E75B6" })]
  });
}

function body(text, opts = {}) {
  const { bold = false, color = "333333", spacing = { before: 60, after: 60 } } = opts;
  return new Paragraph({
    spacing,
    children: [new TextRun({ text, bold, font: "Arial", size: 22, color })]
  });
}

function bullet(text, opts = {}) {
  const { bold = false, color = "333333" } = opts;
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    spacing: { before: 40, after: 40 },
    children: [new TextRun({ text, bold, font: "Arial", size: 22, color })]
  });
}

function divider() {
  return new Paragraph({
    spacing: { before: 120, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "CCCCCC", space: 1 } },
    children: []
  });
}

// ── 이미지 로드 ──────────────────────────────────────────────────────
const img1 = fs.readFileSync(path.join(ARTIFACTS, 'stock_analysis.png'));
const img2 = fs.readFileSync(path.join(ARTIFACTS, 'stock_corr_vol.png'));

// ── 문서 생성 ────────────────────────────────────────────────────────
const doc = new Document({
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [{
          level: 0, format: LevelFormat.BULLET, text: "\u2022",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } }
        }]
      }
    ]
  },
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: "1F3864" },
        paragraph: { spacing: { before: 360, after: 180 }, outlineLevel: 0 }
      },
      {
        id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: "2E75B6" },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 }
      }
    ]
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
      }
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "2E75B6", space: 1 } },
          children: [new TextRun({ text: "Stock Price Analysis Report", font: "Arial", size: 18, color: "2E75B6" })]
        })]
      })
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          border: { top: { style: BorderStyle.SINGLE, size: 4, color: "CCCCCC", space: 1 } },
          children: [
            new TextRun({ text: "Page ", font: "Arial", size: 18, color: "888888" }),
            new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 18, color: "888888" }),
            new TextRun({ text: " / ", font: "Arial", size: 18, color: "888888" }),
            new TextRun({ children: [PageNumber.TOTAL_PAGES], font: "Arial", size: 18, color: "888888" }),
          ]
        })]
      })
    },
    children: [

      // ── 표지 ──────────────────────────────────────────────────────
      new Paragraph({ spacing: { before: 1440, after: 0 }, children: [] }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 240 },
        children: [new TextRun({ text: "Stock Price Analysis Report", bold: true, font: "Arial", size: 56, color: "1F3864" })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 120 },
        children: [new TextRun({ text: "2014.09 ~ 2018.04", font: "Arial", size: 32, color: "2E75B6" })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 720 },
        children: [new TextRun({ text: "19 Stocks | 896 Trading Days", font: "Arial", size: 24, color: "888888" })]
      }),
      divider(),
      new Paragraph({ spacing: { before: 480, after: 0 }, children: [] }),

      // ── 목차 ──────────────────────────────────────────────────────
      new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-2" }),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════════════════════════════════════════════════════
      // 1. 개요
      // ══════════════════════════════════════════════════════════════
      heading1("1. Overview"),
      body("This report analyzes the stock price data of 19 companies over approximately 3 years and 7 months, from September 2014 to April 2018. The analysis covers price trends, total returns, volatility, and inter-stock correlations."),
      new Paragraph({ spacing: { before: 120, after: 60 }, children: [] }),

      // 데이터 개요 테이블
      heading2("1.1 Dataset Summary"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [3000, 6026],
        rows: [
          new TableRow({ children: [
            cell("Item", { bold: true, shade: "D5E8F0", width: 3000 }),
            cell("Details", { bold: true, shade: "D5E8F0", width: 6026 }),
          ]}),
          new TableRow({ children: [
            cell("Analysis Period", { bold: true, width: 3000 }),
            cell("September 19, 2014 ~ April 11, 2018 (approx. 3 years 7 months)", { width: 6026 }),
          ]}),
          new TableRow({ children: [
            cell("Number of Stocks", { bold: true, width: 3000 }),
            cell("19 stocks (AAAA, FF, BBBB, ZZZZ, GG, DDD, WWW, CCC, GGMM, TTT, UUU, SSSS, XXX, RRR, YYY, MM, PPP, JJJ, SSXX)", { width: 6026 }),
          ]}),
          new TableRow({ children: [
            cell("Trading Days", { bold: true, width: 3000 }),
            cell("896 days", { width: 6026 }),
          ]}),
          new TableRow({ children: [
            cell("Data Source", { bold: true, width: 3000 }),
            cell("stock_prices.csv", { width: 6026 }),
          ]}),
        ]
      }),
      new Paragraph({ spacing: { before: 240, after: 0 }, children: [] }),

      // ══════════════════════════════════════════════════════════════
      // 2. 주가 추이 분석
      // ══════════════════════════════════════════════════════════════
      heading1("2. Stock Price Trend Analysis"),
      body("The chart below shows the normalized price trends of all 19 stocks (base = 100 at start date), total return rankings, and a comparison of the top 5 vs. bottom 5 performers."),
      new Paragraph({ spacing: { before: 120, after: 120 }, children: [] }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new ImageRun({
          type: "png",
          data: img1,
          transformation: { width: 620, height: 420 },
          altText: { title: "Stock Price Trend", description: "Normalized price trends and total return bar chart", name: "stock_analysis" }
        })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 60, after: 240 },
        children: [new TextRun({ text: "[Figure 1] Normalized Stock Price Trends & Total Return Rankings", font: "Arial", size: 18, color: "888888", italics: true })]
      }),

      // ── 2.1 총 수익률 TOP 5 ──────────────────────────────────────
      heading2("2.1 Top 5 Performers (by Total Return)"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [900, 1800, 2200, 4126],
        rows: [
          new TableRow({ children: [
            cell("Rank", { bold: true, shade: "1F3864", width: 900, align: AlignmentType.CENTER, color: "FFFFFF" }),
            cell("Ticker", { bold: true, shade: "1F3864", width: 1800, align: AlignmentType.CENTER, color: "FFFFFF" }),
            cell("Total Return", { bold: true, shade: "1F3864", width: 2200, align: AlignmentType.CENTER, color: "FFFFFF" }),
            cell("Remarks", { bold: true, shade: "1F3864", width: 4126, align: AlignmentType.CENTER, color: "FFFFFF" }),
          ]}),
          new TableRow({ children: [
            cell("1", { align: AlignmentType.CENTER, width: 900 }),
            cell("ZZZZ", { bold: true, width: 1800, color: "1F3864" }),
            cell("+330.7%", { bold: true, width: 2200, color: "2E75B6", align: AlignmentType.CENTER }),
            cell("Best performer — over 4x return in 3.5 years", { width: 4126 }),
          ]}),
          new TableRow({ children: [
            cell("2", { align: AlignmentType.CENTER, width: 900 }),
            cell("DDD", { bold: true, width: 1800 }),
            cell("+157.7%", { bold: true, width: 2200, color: "2E75B6", align: AlignmentType.CENTER }),
            cell("High volatility (64.4%) but strong returns", { width: 4126 }),
          ]}),
          new TableRow({ children: [
            cell("3", { align: AlignmentType.CENTER, width: 900 }),
            cell("MM", { bold: true, width: 1800 }),
            cell("+129.2%", { bold: true, width: 2200, color: "2E75B6", align: AlignmentType.CENTER }),
            cell("Steady upward trend", { width: 4126 }),
          ]}),
          new TableRow({ children: [
            cell("4", { align: AlignmentType.CENTER, width: 900 }),
            cell("YYY", { bold: true, width: 1800 }),
            cell("+125.0%", { bold: true, width: 2200, color: "2E75B6", align: AlignmentType.CENTER }),
            cell("Consistent growth with moderate volatility", { width: 4126 }),
          ]}),
          new TableRow({ children: [
            cell("5", { align: AlignmentType.CENTER, width: 900 }),
            cell("FF", { bold: true, width: 1800 }),
            cell("+113.5%", { bold: true, width: 2200, color: "2E75B6", align: AlignmentType.CENTER }),
            cell("Doubled in value over the period", { width: 4126 }),
          ]}),
        ]
      }),
      new Paragraph({ spacing: { before: 240, after: 0 }, children: [] }),

      // ── 2.2 총 수익률 BOTTOM 5 ──────────────────────────────────
      heading2("2.2 Bottom 5 Performers (by Total Return)"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [900, 1800, 2200, 4126],
        rows: [
          new TableRow({ children: [
            cell("Rank", { bold: true, shade: "7F0000", width: 900, align: AlignmentType.CENTER, color: "FFFFFF" }),
            cell("Ticker", { bold: true, shade: "7F0000", width: 1800, align: AlignmentType.CENTER, color: "FFFFFF" }),
            cell("Total Return", { bold: true, shade: "7F0000", width: 2200, align: AlignmentType.CENTER, color: "FFFFFF" }),
            cell("Remarks", { bold: true, shade: "7F0000", width: 4126, align: AlignmentType.CENTER, color: "FFFFFF" }),
          ]}),
          new TableRow({ children: [
            cell("15", { align: AlignmentType.CENTER, width: 900 }),
            cell("XXX", { bold: true, width: 1800 }),
            cell("-9.8%", { bold: true, width: 2200, color: "C00000", align: AlignmentType.CENTER }),
            cell("Slight decline, relatively stable", { width: 4126 }),
          ]}),
          new TableRow({ children: [
            cell("16", { align: AlignmentType.CENTER, width: 900 }),
            cell("GG", { bold: true, width: 1800 }),
            cell("-44.6%", { bold: true, width: 2200, color: "C00000", align: AlignmentType.CENTER }),
            cell("Significant downtrend throughout the period", { width: 4126 }),
          ]}),
          new TableRow({ children: [
            cell("17", { align: AlignmentType.CENTER, width: 900 }),
            cell("UUU", { bold: true, width: 1800 }),
            cell("-51.1%", { bold: true, width: 2200, color: "C00000", align: AlignmentType.CENTER }),
            cell("Lost more than half its value", { width: 4126 }),
          ]}),
          new TableRow({ children: [
            cell("18", { align: AlignmentType.CENTER, width: 900 }),
            cell("RRR", { bold: true, width: 1800 }),
            cell("-78.8%", { bold: true, width: 2200, color: "C00000", align: AlignmentType.CENTER }),
            cell("Severe decline with high volatility (50.0%)", { width: 4126 }),
          ]}),
          new TableRow({ children: [
            cell("19", { align: AlignmentType.CENTER, width: 900 }),
            cell("SSSS", { bold: true, width: 1800 }),
            cell("-87.2%", { bold: true, width: 2200, color: "C00000", align: AlignmentType.CENTER }),
            cell("Worst performer — highest volatility (70.0%) + worst return", { width: 4126 }),
          ]}),
        ]
      }),
      new Paragraph({ spacing: { before: 240, after: 0 }, children: [] }),

      // ── 전체 수익률 순위 ─────────────────────────────────────────
      heading2("2.3 Full Return Rankings"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [600, 1400, 1600, 600, 1400, 1600, 600, 1400, 1426],
        rows: [
          new TableRow({ children: [
            cell("Rank", { bold: true, shade: "D5E8F0", width: 600, align: AlignmentType.CENTER }),
            cell("Ticker", { bold: true, shade: "D5E8F0", width: 1400, align: AlignmentType.CENTER }),
            cell("Return", { bold: true, shade: "D5E8F0", width: 1600, align: AlignmentType.CENTER }),
            cell("Rank", { bold: true, shade: "D5E8F0", width: 600, align: AlignmentType.CENTER }),
            cell("Ticker", { bold: true, shade: "D5E8F0", width: 1400, align: AlignmentType.CENTER }),
            cell("Return", { bold: true, shade: "D5E8F0", width: 1600, align: AlignmentType.CENTER }),
            cell("Rank", { bold: true, shade: "D5E8F0", width: 600, align: AlignmentType.CENTER }),
            cell("Ticker", { bold: true, shade: "D5E8F0", width: 1400, align: AlignmentType.CENTER }),
            cell("Return", { bold: true, shade: "D5E8F0", width: 1426, align: AlignmentType.CENTER }),
          ]}),
          new TableRow({ children: [
            cell("1", { align: AlignmentType.CENTER, width: 600 }),
            cell("ZZZZ", { bold: true, width: 1400 }),
            cell("+330.7%", { width: 1600, color: "2E75B6", bold: true }),
            cell("8", { align: AlignmentType.CENTER, width: 600 }),
            cell("CCC", { width: 1400 }),
            cell("+85.4%", { width: 1600, color: "2E75B6" }),
            cell("15", { align: AlignmentType.CENTER, width: 600 }),
            cell("XXX", { width: 1400 }),
            cell("-9.8%", { width: 1426, color: "C00000" }),
          ]}),
          new TableRow({ children: [
            cell("2", { align: AlignmentType.CENTER, width: 600 }),
            cell("DDD", { bold: true, width: 1400 }),
            cell("+157.7%", { width: 1600, color: "2E75B6", bold: true }),
            cell("9", { align: AlignmentType.CENTER, width: 600 }),
            cell("AAAA", { width: 1400 }),
            cell("+82.0%", { width: 1600, color: "2E75B6" }),
            cell("16", { align: AlignmentType.CENTER, width: 600 }),
            cell("GG", { width: 1400 }),
            cell("-44.6%", { width: 1426, color: "C00000" }),
          ]}),
          new TableRow({ children: [
            cell("3", { align: AlignmentType.CENTER, width: 600 }),
            cell("MM", { bold: true, width: 1400 }),
            cell("+129.2%", { width: 1600, color: "2E75B6", bold: true }),
            cell("10", { align: AlignmentType.CENTER, width: 600 }),
            cell("SSXX", { width: 1400 }),
            cell("+66.4%", { width: 1600, color: "2E75B6" }),
            cell("17", { align: AlignmentType.CENTER, width: 600 }),
            cell("UUU", { width: 1400 }),
            cell("-51.1%", { width: 1426, color: "C00000" }),
          ]}),
          new TableRow({ children: [
            cell("4", { align: AlignmentType.CENTER, width: 600 }),
            cell("YYY", { bold: true, width: 1400 }),
            cell("+125.0%", { width: 1600, color: "2E75B6", bold: true }),
            cell("11", { align: AlignmentType.CENTER, width: 600 }),
            cell("PPP", { width: 1400 }),
            cell("+33.6%", { width: 1600, color: "2E75B6" }),
            cell("18", { align: AlignmentType.CENTER, width: 600 }),
            cell("RRR", { width: 1400 }),
            cell("-78.8%", { width: 1426, color: "C00000" }),
          ]}),
          new TableRow({ children: [
            cell("5", { align: AlignmentType.CENTER, width: 600 }),
            cell("FF", { bold: true, width: 1400 }),
            cell("+113.5%", { width: 1600, color: "2E75B6", bold: true }),
            cell("12", { align: AlignmentType.CENTER, width: 600 }),
            cell("GGMM", { width: 1400 }),
            cell("+33.3%", { width: 1600, color: "2E75B6" }),
            cell("19", { align: AlignmentType.CENTER, width: 600 }),
            cell("SSSS", { width: 1400 }),
            cell("-87.2%", { width: 1426, color: "C00000" }),
          ]}),
          new TableRow({ children: [
            cell("6", { align: AlignmentType.CENTER, width: 600 }),
            cell("JJJ", { width: 1400 }),
            cell("+99.1%", { width: 1600, color: "2E75B6" }),
            cell("13", { align: AlignmentType.CENTER, width: 600 }),
            cell("WWW", { width: 1400 }),
            cell("+22.7%", { width: 1600, color: "2E75B6" }),
            cell("", { width: 600 }),
            cell("", { width: 1400 }),
            cell("", { width: 1426 }),
          ]}),
          new TableRow({ children: [
            cell("7", { align: AlignmentType.CENTER, width: 600 }),
            cell("BBBB", { width: 1400 }),
            cell("+86.8%", { width: 1600, color: "2E75B6" }),
            cell("14", { align: AlignmentType.CENTER, width: 600 }),
            cell("TTT", { width: 1400 }),
            cell("+20.7%", { width: 1600, color: "2E75B6" }),
            cell("", { width: 600 }),
            cell("", { width: 1400 }),
            cell("", { width: 1426 }),
          ]}),
        ]
      }),
      new Paragraph({ children: [new PageBreak()] }),

      // ══════════════════════════════════════════════════════════════
      // 3. 변동성 & 상관관계 분석
      // ══════════════════════════════════════════════════════════════
      heading1("3. Volatility & Correlation Analysis"),
      body("The chart below presents the annualized volatility of each stock and the return correlation heatmap between all 19 stocks."),
      new Paragraph({ spacing: { before: 120, after: 120 }, children: [] }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new ImageRun({
          type: "png",
          data: img2,
          transformation: { width: 620, height: 280 },
          altText: { title: "Correlation & Volatility", description: "Heatmap and volatility bar chart", name: "stock_corr_vol" }
        })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 60, after: 240 },
        children: [new TextRun({ text: "[Figure 2] Correlation Heatmap & Annualized Volatility", font: "Arial", size: 18, color: "888888", italics: true })]
      }),

      // ── 3.1 변동성 ───────────────────────────────────────────────
      heading2("3.1 Annualized Volatility Rankings"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [600, 1600, 1800, 600, 1600, 1800, 600, 1600, 826],
        rows: [
          new TableRow({ children: [
            cell("Rank", { bold: true, shade: "D5E8F0", width: 600, align: AlignmentType.CENTER }),
            cell("Ticker", { bold: true, shade: "D5E8F0", width: 1600, align: AlignmentType.CENTER }),
            cell("Volatility", { bold: true, shade: "D5E8F0", width: 1800, align: AlignmentType.CENTER }),
            cell("Rank", { bold: true, shade: "D5E8F0", width: 600, align: AlignmentType.CENTER }),
            cell("Ticker", { bold: true, shade: "D5E8F0", width: 1600, align: AlignmentType.CENTER }),
            cell("Volatility", { bold: true, shade: "D5E8F0", width: 1800, align: AlignmentType.CENTER }),
            cell("Rank", { bold: true, shade: "D5E8F0", width: 600, align: AlignmentType.CENTER }),
            cell("Ticker", { bold: true, shade: "D5E8F0", width: 1600, align: AlignmentType.CENTER }),
            cell("Volatility", { bold: true, shade: "D5E8F0", width: 826, align: AlignmentType.CENTER }),
          ]}),
          new TableRow({ children: [
            cell("1", { align: AlignmentType.CENTER, width: 600 }),
            cell("SSSS", { bold: true, width: 1600, color: "C00000" }),
            cell("70.0%", { width: 1800, color: "C00000", bold: true }),
            cell("8", { align: AlignmentType.CENTER, width: 600 }),
            cell("GGMM", { width: 1600 }),
            cell("24.8%", { width: 1800 }),
            cell("15", { align: AlignmentType.CENTER, width: 600 }),
            cell("MM", { width: 1600 }),
            cell("20.2%", { width: 826 }),
          ]}),
          new TableRow({ children: [
            cell("2", { align: AlignmentType.CENTER, width: 600 }),
            cell("DDD", { bold: true, width: 1600, color: "C00000" }),
            cell("64.4%", { width: 1800, color: "C00000", bold: true }),
            cell("9", { align: AlignmentType.CENTER, width: 600 }),
            cell("FF", { width: 1600 }),
            cell("25.5%", { width: 1800 }),
            cell("16", { align: AlignmentType.CENTER, width: 600 }),
            cell("SSXX", { width: 1600 }),
            cell("19.9%", { width: 826 }),
          ]}),
          new TableRow({ children: [
            cell("3", { align: AlignmentType.CENTER, width: 600 }),
            cell("RRR", { width: 1600, color: "C00000" }),
            cell("50.0%", { width: 1800, color: "C00000" }),
            cell("10", { align: AlignmentType.CENTER, width: 600 }),
            cell("AAAA", { width: 1600 }),
            cell("23.1%", { width: 1800 }),
            cell("17", { align: AlignmentType.CENTER, width: 600 }),
            cell("XXX", { width: 1600 }),
            cell("19.0%", { width: 826 }),
          ]}),
          new TableRow({ children: [
            cell("4", { align: AlignmentType.CENTER, width: 600 }),
            cell("UUU", { width: 1600, color: "C00000" }),
            cell("42.2%", { width: 1800, color: "C00000" }),
            cell("11", { align: AlignmentType.CENTER, width: 600 }),
            cell("JJJ", { width: 1600 }),
            cell("21.6%", { width: 1800 }),
            cell("18", { align: AlignmentType.CENTER, width: 600 }),
            cell("PPP", { width: 1600 }),
            cell("17.5%", { width: 826 }),
          ]}),
          new TableRow({ children: [
            cell("5", { align: AlignmentType.CENTER, width: 600 }),
            cell("YYY", { width: 1600 }),
            cell("35.7%", { width: 1800 }),
            cell("12", { align: AlignmentType.CENTER, width: 600 }),
            cell("GG", { width: 1600 }),
            cell("21.2%", { width: 1800 }),
            cell("19", { align: AlignmentType.CENTER, width: 600 }),
            cell("TTT", { width: 1600 }),
            cell("16.3%", { width: 826, color: "2E75B6" }),
          ]}),
          new TableRow({ children: [
            cell("6", { align: AlignmentType.CENTER, width: 600 }),
            cell("BBBB", { width: 1600 }),
            cell("31.8%", { width: 1800 }),
            cell("13", { align: AlignmentType.CENTER, width: 600 }),
            cell("WWW", { width: 1600 }),
            cell("20.3%", { width: 1800 }),
            cell("", { width: 600 }),
            cell("", { width: 1600 }),
            cell("", { width: 826 }),
          ]}),
          new TableRow({ children: [
            cell("7", { align: AlignmentType.CENTER, width: 600 }),
            cell("ZZZZ", { width: 1600 }),
            cell("28.9%", { width: 1800 }),
            cell("14", { align: AlignmentType.CENTER, width: 600 }),
            cell("CCC", { width: 1600 }),
            cell("26.4%", { width: 1800 }),
            cell("", { width: 600 }),
            cell("", { width: 1600 }),
            cell("", { width: 826 }),
          ]}),
        ]
      }),
      new Paragraph({ spacing: { before: 240, after: 0 }, children: [] }),

      // ── 3.2 상관관계 ─────────────────────────────────────────────
      heading2("3.2 Correlation Analysis"),
      body("Key findings from the return correlation heatmap:"),
      bullet("Most stocks show positive correlation, indicating they tend to move together with the broader market."),
      bullet("SSSS, DDD, and RRR exhibit lower correlations with other stocks, suggesting more independent price movements."),
      bullet("Stocks with similar business characteristics tend to cluster with higher mutual correlations."),
      bullet("Diversification benefits are limited when most stocks are positively correlated."),
      new Paragraph({ spacing: { before: 240, after: 0 }, children: [] }),

      // ══════════════════════════════════════════════════════════════
      // 4. 핵심 인사이트
      // ══════════════════════════════════════════════════════════════
      heading1("4. Key Insights"),

      heading2("4.1 High Return Stocks"),
      bullet("ZZZZ achieved the highest return of +330.7%, growing more than 4x in approximately 3.5 years."),
      bullet("DDD delivered +157.7% return despite high volatility (64.4%), demonstrating a high-risk, high-reward profile."),
      bullet("MM, YYY, and FF all more than doubled in value, showing consistent upward trends."),
      new Paragraph({ spacing: { before: 120, after: 0 }, children: [] }),

      heading2("4.2 High Risk Stocks"),
      bullet("SSSS recorded the worst performance: highest volatility (70.0%) combined with the worst return (-87.2%)."),
      bullet("RRR and UUU also showed severe declines (-78.8%, -51.1%) with high volatility."),
      bullet("These stocks represent the classic high-risk, low-reward scenario."),
      new Paragraph({ spacing: { before: 120, after: 0 }, children: [] }),

      heading2("4.3 Stable Low-Volatility Stocks"),
      bullet("TTT (16.3%), PPP (17.5%), and XXX (19.0%) showed the lowest volatility."),
      bullet("While their returns were modest, they provided stability and capital preservation."),
      bullet("These stocks are suitable for risk-averse investors seeking steady, predictable performance."),
      new Paragraph({ spacing: { before: 120, after: 0 }, children: [] }),

      heading2("4.4 Overall Market Trend"),
      bullet("Most stocks showed an upward trend from 2016 onward, suggesting a broad market recovery."),
      bullet("The period from late 2014 to early 2016 was relatively volatile with mixed performance."),
      bullet("The divergence between winners and losers widened significantly after 2016."),
      new Paragraph({ spacing: { before: 240, after: 0 }, children: [] }),

      // ══════════════════════════════════════════════════════════════
      // 5. 결론
      // ══════════════════════════════════════════════════════════════
      heading1("5. Conclusion"),
      body("This analysis of 19 stocks over approximately 3.5 years (September 2014 to April 2018) reveals significant performance divergence across the portfolio. The key takeaways are:"),
      new Paragraph({ spacing: { before: 120, after: 0 }, children: [] }),
      bullet("ZZZZ was the standout winner with +330.7% total return, while SSSS was the worst performer at -87.2%."),
      bullet("High volatility does not always translate to high returns — DDD succeeded while SSSS and RRR failed."),
      bullet("Low-volatility stocks (TTT, PPP) provided stability but limited upside."),
      bullet("Most stocks are positively correlated, limiting diversification benefits within this portfolio."),
      bullet("The post-2016 period saw broad market gains, benefiting most stocks except the structural underperformers."),
      new Paragraph({ spacing: { before: 240, after: 0 }, children: [] }),
      divider(),
      new Paragraph({
        alignment: AlignmentType.RIGHT,
        spacing: { before: 120, after: 0 },
        children: [new TextRun({ text: "Generated by AI Analysis Agent", font: "Arial", size: 18, color: "AAAAAA", italics: true })]
      }),
    ]
  }]
});

// 저장
Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync(path.join(ARTIFACTS, 'stock_report.docx'), buffer);
  console.log('stock_report.docx saved successfully!');
});
