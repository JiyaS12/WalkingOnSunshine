import { cleanup, render, screen } from "@testing-library/react";
import { type ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PrivacyPolicy, { metadata as privacyMetadata } from "../privacy/page";
import TermsAndConditions, { metadata as termsMetadata } from "../terms/page";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: ComponentProps<"a">) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next/image", () => ({
  default: ({ alt }: ComponentProps<"img">) => <span role="img" aria-label={alt} />,
}));

afterEach(cleanup);

describe("SMS compliance pages", () => {
  it("privacy policy names the brand, the data collected and the no-marketing statement", () => {
    render(<PrivacyPolicy />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Privacy Policy");
    expect(privacyMetadata.title).toBe("Privacy Policy | Sana");
    expect(screen.getByRole("heading", { name: "Information we collect" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "How we use it" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "We do not sell or share your SMS opt-in data or personal information with third parties for marketing purposes.",
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/Sana/).length).toBeGreaterThan(0);
    for (const link of screen.getAllByRole("link", { name: "Terms & Conditions" })) {
      expect(link).toHaveAttribute("href", "/terms");
    }
  });

  it("terms include an SMS Terms section, the rates disclosure and the brand", () => {
    render(<TermsAndConditions />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Terms & Conditions");
    expect(termsMetadata.title).toBe("Terms & Conditions | Sana");
    expect(screen.getByRole("heading", { name: "SMS Terms" })).toBeInTheDocument();
    expect(screen.getByText("Message and data rates may apply.")).toBeInTheDocument();
    expect(screen.getAllByText(/Sana/).length).toBeGreaterThan(0);
    for (const link of screen.getAllByRole("link", { name: "Privacy Policy" })) {
      expect(link).toHaveAttribute("href", "/privacy");
    }
  });
});
