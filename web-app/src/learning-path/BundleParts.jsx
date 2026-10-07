// A concept's Standard version, shown the same way everywhere: one entry per
// learning object it is taught as, numbered and titled, with that object's own
// text. A one-object concept is just its text. Used by the learning path card,
// the Content versions Standard box and the Questions step card, matching the
// published dialog's one-player-per-object list.
export default function BundleParts({
  parts,
  fallback = "",
  className = "",
  showSingleTitle = false,
}) {
  const shown = (parts || []).filter((part) => (part.text || "").trim());
  if (shown.length <= 1 && !showSingleTitle) {
    const text = shown[0]?.text || fallback;
    return text ? <p className={`bundle-text ${className}`.trim()}>{text}</p> : null;
  }
  if (shown.length === 0) {
    return fallback ? <p className={`bundle-text ${className}`.trim()}>{fallback}</p> : null;
  }
  return (
    <ol className={`bundle-parts ${className}`.trim()}>
      {shown.map((part, index) => (
        <li key={part.id ?? index}>
          <strong>{index + 1}. {part.title || `Part ${index + 1}`}</strong>
          <p>{part.text}</p>
        </li>
      ))}
    </ol>
  );
}
