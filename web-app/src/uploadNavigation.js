// Use the server's identity (including duplicate uploads), never the largest ID.
export function uploadedMaterialFromResponse(course) {
  return (course.materials || []).find(
    material => String(material.id) === String(course.uploaded_material_id),
  );
}

export function uploadedMaterialUrl(courseId, material) {
  if (!material?.outline_node || material.status === "failed") return null;
  return `/courses/${encodeURIComponent(courseId)}/topics/${encodeURIComponent(material.outline_node)}?material=${encodeURIComponent(material.id)}`;
}
