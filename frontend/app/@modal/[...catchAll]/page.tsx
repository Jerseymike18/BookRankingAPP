// Any soft navigation to a route that isn't intercepted lands here and closes
// the sheet — e.g. following the "Import your Goodreads library" link from
// inside the Add-a-Book sheet.
export default function CatchAll() {
  return null;
}
