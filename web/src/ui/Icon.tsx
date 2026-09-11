export function Icon({
  name = "file",
}: {
  name?: "file" | "check" | "book" | "clock" | "shield" | "arrow" | "home" | "person" | "search";
}) {
  const paths = {
    file: "M14 2H5v20h14V7l-5-5Zm0 0v6h5M8 12h8M8 16h6",
    check: "m5 12 4 4L19 6",
    book: "M12 5c-4-3-7-3-10-2v16c4-1 7 0 10 2m0-16c4-3 7-3 10-2v16c-4-1-7 0-10 2V5Z",
    clock: "M3 8a9 9 0 1 1-1 8M3 2v6h6m3-2v7l4 2",
    shield: "M12 2 3 6v6c0 5 5 8 9 10 4-2 9-5 9-10V6l-9-4Zm-5 10 3 3 7-7",
    arrow: "M4 12h16m-6-6 6 6-6 6",
    home: "m2 11 10-9 10 9M5 9v12h5v-7h4v7h5V9",
    person: "M12 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10ZM3 22v-2a9 7 0 0 1 18 0v2",
    search: "M16 16 22 22M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z",
  };
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
