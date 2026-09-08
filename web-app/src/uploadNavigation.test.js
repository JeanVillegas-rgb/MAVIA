import test from "node:test";
import assert from "node:assert/strict";
import * as web from "./uploadNavigation.js";
import * as legacy from "../../frontend/src/uploadNavigation.js";

for (const [name, api] of [["web-app", web], ["frontend", legacy]]) {
  test(`${name}: selects exact uploaded file, including reused older files`, () => {
    const older = { id: 4, outline_node: 8, status: "completed" };
    const course = { uploaded_material_id: "4", materials: [{ id: 90 }, older] };
    assert.equal(api.uploadedMaterialFromResponse(course), older);
    assert.equal(api.uploadedMaterialUrl(2, older), "/courses/2/topics/8?material=4");
  });
  test(`${name}: no redirect for failed or unplaced uploads`, () => {
    assert.equal(api.uploadedMaterialUrl(2, { id: 4, outline_node: 8, status: "failed" }), null);
    assert.equal(api.uploadedMaterialUrl(2, { id: 4 }), null);
    assert.equal(api.uploadedMaterialUrl(2, undefined), null);
    assert.equal(api.uploadedMaterialFromResponse({ materials: [] }), undefined);
  });
}
