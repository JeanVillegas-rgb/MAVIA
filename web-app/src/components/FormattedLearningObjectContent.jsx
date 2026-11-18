import { Fragment } from "react";

function renderInlineFormatting(text) {
  return String(text || "").split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((part, index) => (
    part.startsWith("**") && part.endsWith("**")
      ? <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>
      : <Fragment key={`${part}-${index}`}>{part}</Fragment>
  ));
}

export default function FormattedLearningObjectContent({ content, className = "" }) {
  const blocks = [];
  const stack = [];
  // Only recognize source markers at the start of a line. Never renumber them.
  const markerPattern = /^([ \t]*)([-*+\u2022\u2023\u25E6\u2043\u2219\u25AA\u25CF\u25CB\u25A0\u25C6\u25C7]|(?:\d+|[a-z]|[ivxlcdm]+)[.)]|\((?:\d+|[a-z]|[ivxlcdm]+)\))\s+(.+)$/i;
  // Accept "1.Solid" too, without mistaking "3.14" for a list item.
  const compactNumberPattern = /^([ \t]*)(\d+[.)])([^\d\s].*)$/;

  for (const line of String(content || "No narration content.").split(/\r?\n/)) {
    const match = line.match(markerPattern) || line.match(compactNumberPattern);
    if (!match) {
      stack.length = 0;
      if (line.trim()) blocks.push({ text: line.trim() });
      continue;
    }

    const [, whitespace, marker, text] = match;
    const indent = whitespace.replace(/\t/g, "  ").length;
    const tag = /[\da-z]/i.test(marker) ? "ol" : "ul";
    while (stack.length && (indent < stack.at(-1).indent
      || (indent === stack.at(-1).indent && tag !== stack.at(-1).node.tag))) stack.pop();
    if (!stack.length || indent > stack.at(-1).indent) {
      const node = { tag, items: [] };
      const parent = stack.at(-1)?.node.items.at(-1);
      (parent ? parent.children : blocks).push(node);
      stack.push({ indent, node });
    }
    stack.at(-1).node.items.push({ marker, text, children: [] });
  }

  function renderBlocks(nodes) {
    return nodes.map((node, index) => {
      if (!node.tag) {
        const heading = node.text.endsWith(":") && node.text.length <= 90;
        return <p key={index} className={heading ? "formatted-content-heading" : undefined}>
          {heading ? <strong>{renderInlineFormatting(node.text)}</strong> : renderInlineFormatting(node.text)}
        </p>;
      }
      const ListTag = node.tag;
      return <ListTag key={index} className="formatted-content-list" style={{ listStyle: "none" }}>
        {node.items.map((item, itemIndex) => (
          <li key={itemIndex} style={{ display: "grid", gridTemplateColumns: "max-content minmax(0, 1fr)", columnGap: "0.5em" }}>
            <span>{item.marker}</span>
            <div>{renderInlineFormatting(item.text)}</div>
            {item.children.length > 0 && <div style={{ gridColumn: 2 }}>{renderBlocks(item.children)}</div>}
          </li>
        ))}
      </ListTag>;
    });
  }

  return <div className={`formatted-learning-object-content ${className}`.trim()}>{renderBlocks(blocks)}</div>;
}
