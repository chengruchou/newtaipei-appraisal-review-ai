import { createContext, useCallback, useContext, type ReactNode } from "react";

export type Language = "zh" | "en";
const LanguageContext = createContext<Language>("en");

export function LanguageProvider({
  language,
  children,
}: {
  language: Language;
  children: ReactNode;
}) {
  return <LanguageContext.Provider value={language}>{children}</LanguageContext.Provider>;
}

export function useText() {
  const language = useContext(LanguageContext);
  return useCallback(
    (english: string, chinese: string) => (language === "zh" ? chinese : english),
    [language],
  );
}
