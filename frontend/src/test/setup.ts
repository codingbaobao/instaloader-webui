import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "./server";

if (HTMLDialogElement.prototype.showModal === undefined) {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.open = true;
  };
}

if (HTMLDialogElement.prototype.close === undefined) {
  HTMLDialogElement.prototype.close = function close() {
    this.open = false;
  };
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
