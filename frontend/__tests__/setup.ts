import "@testing-library/jest-dom/vitest";

// candidate-gallery's materialize gate reads this at module-import time, so
// it must be set before any test file's imports resolve (setupFiles run
// before that, unlike a per-file beforeEach).
process.env.NEXT_PUBLIC_ENABLE_MATERIALIZE = "true";
