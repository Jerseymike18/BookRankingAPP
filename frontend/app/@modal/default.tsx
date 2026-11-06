// The @modal slot renders nothing unless a route intercepts into it (see
// (.)add-book). Needed so a hard load of any page doesn't 404 the slot.
export default function Default() {
  return null;
}
