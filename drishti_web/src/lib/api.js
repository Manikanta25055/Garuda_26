// Same-origin only. The drishti_session cookie is host-scoped, so a call to
// api.veeramanikanta.in would not carry it — login would appear to succeed and
// every subsequent request would 401.

export class ApiError extends Error {
  constructor(status, detail, offline = false) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.offline = offline;
  }
}

let unauthorizedHandler = null;
let signedOut = false;

export function onUnauthorized(handler) {
  unauthorizedHandler = handler;
  signedOut = false;
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      ...options,
    });
  } catch {
    throw new ApiError(0, "Can't reach the house right now.", true);
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    if (response.status === 401) {
      // A screen loads several things at once. Without this guard every one of
      // them would bounce the app back to login, and the second bounce would
      // land on a screen that had already changed under it.
      if (unauthorizedHandler && !signedOut) {
        signedOut = true;
        unauthorizedHandler();
      }
    }
    throw new ApiError(response.status, body?.detail ?? "Something went wrong.");
  }
  signedOut = false;
  return body;
}

export const api = {
  get: (path) => request(path),
  post: (path, body) =>
    request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    }),
  del: (path) => request(path, { method: "DELETE" }),
};
