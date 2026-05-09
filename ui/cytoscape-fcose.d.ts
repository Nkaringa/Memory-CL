// Ambient type declaration for `cytoscape-fcose`.
//
// The package ships JS without a corresponding `@types/cytoscape-fcose`
// on DefinitelyTyped, so TypeScript's strict typecheck (used by
// `next build`) fails with TS7016 "implicitly has an 'any' type"
// unless we declare the module ourselves.
//
// We use it only as a layout extension — `cy.use(fcose)` plus
// `layout: { name: "fcose", ... }` — so a permissive declaration is
// sufficient. No runtime properties of the import are accessed
// directly.
declare module "cytoscape-fcose";
