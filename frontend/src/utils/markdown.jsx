import React from 'react';

/**
 * Lightweight, safe Markdown renderer for BIS technical answers.
 * Parses headers, bold IS tags, bullet/numbered lists, markdown tables, code blocks, links.
 */
export function renderMarkdownToNodes(content) {
  if (!content) return null;

  const lines = content.split('\n');
  const elements = [];
  let inCodeBlock = false;
  let codeBuffer = [];
  let inTable = false;
  let tableRows = [];
  let listItems = [];
  let listType = null; // 'ul' | 'ol'

  const flushList = () => {
    if (listItems.length > 0) {
      if (listType === 'ol') {
        elements.push(
          <ol key={`ol-${elements.length}`} className="md-ol">
            {listItems.map((item, idx) => (
              <li key={idx}>{parseInlineFormatting(item)}</li>
            ))}
          </ol>
        );
      } else {
        elements.push(
          <ul key={`ul-${elements.length}`} className="md-ul">
            {listItems.map((item, idx) => (
              <li key={idx}>{parseInlineFormatting(item)}</li>
            ))}
          </ul>
        );
      }
      listItems = [];
      listType = null;
    }
  };

  const flushTable = () => {
    if (tableRows.length > 0) {
      const header = tableRows[0];
      const dataRows = tableRows.slice(1).filter((r) => !r.every((c) => /^[-:\s]+$/.test(c)));

      elements.push(
        <div key={`table-wrapper-${elements.length}`} className="md-table-wrapper">
          <table className="md-table">
            <thead>
              <tr>
                {header.map((col, idx) => (
                  <th key={idx}>{parseInlineFormatting(col)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dataRows.map((row, rIdx) => (
                <tr key={rIdx}>
                  {row.map((col, cIdx) => (
                    <td key={cIdx}>{parseInlineFormatting(col)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      tableRows = [];
      inTable = false;
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // Code block toggle
    if (trimmed.startsWith('```')) {
      flushList();
      flushTable();
      if (inCodeBlock) {
        elements.push(
          <pre key={`code-${elements.length}`} className="md-pre">
            <code>{codeBuffer.join('\n')}</code>
          </pre>
        );
        codeBuffer = [];
        inCodeBlock = false;
      } else {
        inCodeBlock = true;
      }
      continue;
    }

    if (inCodeBlock) {
      codeBuffer.push(line);
      continue;
    }

    // Markdown Table detection
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      flushList();
      inTable = true;
      const cells = trimmed
        .slice(1, -1)
        .split('|')
        .map((c) => c.trim());
      tableRows.push(cells);
      continue;
    } else if (inTable) {
      flushTable();
    }

    // Unordered List (- or * or •)
    const ulMatch = line.match(/^(\s*)([-*•])\s+(.+)$/);
    if (ulMatch) {
      if (listType === 'ol') flushList();
      listType = 'ul';
      listItems.push(ulMatch[3]);
      continue;
    }

    // Ordered List (1. 2. etc)
    const olMatch = line.match(/^(\s*)(\d+)\.\s+(.+)$/);
    if (olMatch) {
      if (listType === 'ul') flushList();
      listType = 'ol';
      listItems.push(olMatch[3]);
      continue;
    }

    // If not list line, flush pending list
    flushList();

    // Blank line
    if (!trimmed) {
      continue;
    }

    // Headings
    if (trimmed.startsWith('### ')) {
      elements.push(
        <h4 key={`h4-${elements.length}`} className="md-h4">
          {parseInlineFormatting(trimmed.slice(4))}
        </h4>
      );
      continue;
    }
    if (trimmed.startsWith('## ')) {
      elements.push(
        <h3 key={`h3-${elements.length}`} className="md-h3">
          {parseInlineFormatting(trimmed.slice(3))}
        </h3>
      );
      continue;
    }
    if (trimmed.startsWith('# ')) {
      elements.push(
        <h2 key={`h2-${elements.length}`} className="md-h2">
          {parseInlineFormatting(trimmed.slice(2))}
        </h2>
      );
      continue;
    }

    // Blockquote
    if (trimmed.startsWith('> ')) {
      elements.push(
        <blockquote key={`bq-${elements.length}`} className="md-blockquote">
          {parseInlineFormatting(trimmed.slice(2))}
        </blockquote>
      );
      continue;
    }

    // Regular Paragraph
    elements.push(
      <p key={`p-${elements.length}`} className="md-p">
        {parseInlineFormatting(line)}
      </p>
    );
  }

  // Final flush
  flushList();
  flushTable();
  if (inCodeBlock && codeBuffer.length > 0) {
    elements.push(
      <pre key={`code-${elements.length}`} className="md-pre">
        <code>{codeBuffer.join('\n')}</code>
      </pre>
    );
  }

  return elements;
}

/**
 * Parses inline formatting like **bold**, *italic*, `code`, [link](url), and Indian Standard numbers.
 */
function parseInlineFormatting(text) {
  if (!text) return null;

  // Split into tokens based on markdown links, bold, code, and IS numbers
  const regex = /(\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\bIS(?:\s+|\/)[A-Z0-9]+(?:\s*\(Part\s*\d+\))?(?::\s*\d{4})?\b)/g;
  const parts = text.split(regex);

  return parts.map((part, index) => {
    if (!part) return null;

    // Link: [label](url)
    const linkMatch = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (linkMatch) {
      return (
        <a
          key={index}
          href={linkMatch[2]}
          target="_blank"
          rel="noopener noreferrer"
          className="md-link"
        >
          {linkMatch[1]}
        </a>
      );
    }

    // Bold: **text**
    if (part.startsWith('**') && part.endsWith('**') && part.length >= 4) {
      return (
        <strong key={index} className="md-bold">
          {part.slice(2, -2)}
        </strong>
      );
    }

    // Italic: *text*
    if (part.startsWith('*') && part.endsWith('*') && part.length >= 2) {
      return (
        <em key={index} className="md-italic">
          {part.slice(1, -1)}
        </em>
      );
    }

    // Code: `code`
    if (part.startsWith('`') && part.endsWith('`') && part.length >= 2) {
      return (
        <code key={index} className="md-code">
          {part.slice(1, -1)}
        </code>
      );
    }

    // Standard IS highlight badge (e.g. IS 16102, IS 694)
    if (/^IS(?:\s+|\/)[A-Z0-9]+(?:\s*\(Part\s*\d+\))?(?::\s*\d{4})?$/i.test(part.trim())) {
      return (
        <span key={index} className="is-badge" title="Indian Standard Reference">
          {part}
        </span>
      );
    }

    return part;
  });
}
