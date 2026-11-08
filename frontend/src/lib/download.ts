// A file of the server saved as a download. Fetched as a blob first: a plain <a download> on the URL opens the file
// instead on some devices (Firefox on mobile), the blob is saved everywhere.
const REVOKE_AFTER_MS = 1000; // the browser has taken the object URL for the download by then

export async function saveAs(url: string, name: string): Promise<void> {
  const response = await fetch(url, { credentials: "same-origin" });
  if (!response.ok) throw new Error(`${response.status}: ${response.statusText}`);
  const link = document.createElement("a");
  link.href = URL.createObjectURL(await response.blob());
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(link.href), REVOKE_AFTER_MS);
}
