const normalize = (value) => String(value || "").trim().replace(/\s+/g, " ").toLowerCase();

// Display-only: never clear source titles/sections used by grouping or generation.
export function objectHeadings(item, previousItem, parentTitle = "") {
  const section = (item.section_title || "").trim();
  const title = (item.title || "").trim();
  const sectionAlreadyShown = normalize(section) === normalize(parentTitle)
    || normalize(section) === normalize(previousItem?.section_title);
  const repeatedTitle = previousItem
    && normalize(title) === normalize(previousItem.title)
    && normalize(section) === normalize(previousItem.section_title);

  return {
    section: section && !sectionAlreadyShown ? section : "",
    title: normalize(title) === normalize(section)
      || normalize(title) === normalize(parentTitle)
      || repeatedTitle ? "" : title,
  };
}
