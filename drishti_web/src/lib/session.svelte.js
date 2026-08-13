import { api, onUnauthorized } from "./api.js";

class Session {
  username = $state("");
  role = $state("");
  signedIn = $state(false);

  clear() {
    this.username = "";
    this.role = "";
    this.signedIn = false;
  }

  async signIn(username, password) {
    const body = await api.post("/api/drishti/login", { username, password });
    this.username = body.username;
    this.role = body.role;
    this.signedIn = true;
  }

  async signOut() {
    try {
      await api.post("/api/drishti/logout");
    } catch {
      // Swallowed. The person asked to leave; a network error on the way out
      // is not something they can act on, and leaving them apparently signed
      // in is worse than a cookie that outlives the screen.
    }
    this.clear();
  }
}

export const session = new Session();

onUnauthorized(() => session.clear());
