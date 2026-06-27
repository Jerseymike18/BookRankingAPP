import { fetchBooks } from "@/lib/api";
import EditRatingsClient from "./EditRatingsClient";

export default async function EditRatingsPage() {
  const data = await fetchBooks();
  return <EditRatingsClient data={data} />;
}
