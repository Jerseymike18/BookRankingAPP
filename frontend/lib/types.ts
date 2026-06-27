export type CategoryComponents = Record<string, Record<string, number | null>>;

export interface Book {
  rank: number;
  title: string;
  author: string;
  genre: string;
  series: string;
  words: number | null;
  year: number | null;
  wa: number;
  components: CategoryComponents;
}

export interface BooksResponse {
  books: Book[];
  genres: string[];
  category_order: string[];
}

export interface BookScoresResponse {
  title: string;
  author: string;
  genre: string;
  wa: number;
  components: CategoryComponents;
}

export interface LookupResult {
  title: string;
  author: string;
  genre: string | null;
  words: number | null;
  series: string;
  blurb: string;
}
