// A concept's Standard version, shown the same way everywhere: one entry per
// learning object it is taught as, keeping only the source's numbering. Shared headings
// appear once; distinct object titles are kept. A one-object concept is just its
// text. Used by the learning path card,
// the Content versions Standard box and the Questions step card, matching the
// published dialog's one-player-per-object list.
import { objectHeadings } from "./objectHeadings";
import FormattedLearningObjectContent from "../components/FormattedLearningObjectContent";

export default function BundleParts({
  parts,
  fallback = "",
  className = "",
  showSingleTitle = false,
  parentTitle = "",
}) {
  const shown = (parts || []).filter((part) => (part.text || "").trim());
  if (shown.length <= 1 && !showSingleTitle) {
    const text = shown[0]?.text || fallback;
    return text ? <FormattedLearningObjectContent content={text} className={`bundle-text ${className}`.trim()} /> : null;
  }
  if (shown.length === 0) {
    return fallback ? <FormattedLearningObjectContent content={fallback} className={`bundle-text ${className}`.trim()} /> : null;
  }
  return (
    <div className={`bundle-parts ${className}`.trim()}>
      {shown.map((part, index) => {
        const headings = objectHeadings(part, shown[index - 1], parentTitle);
        return (
          <div className="bundle-part" key={part.id ?? index}>
            {headings.section && <h6>{headings.section}</h6>}
            {headings.title && <strong className="bundle-part-title">{headings.title}</strong>}
            <FormattedLearningObjectContent content={part.text} />
          </div>
        );
      })}
    </div>
  );
}
