import type { Metadata } from "next";
import Link from "next/link";
import { BRAND, LegalPage } from "@/app/components/LegalPage";

export const metadata: Metadata = {
  title: `Terms & Conditions | ${BRAND}`,
  description: `Terms & Conditions for the ${BRAND} check-in call, SMS link and walking assessment.`,
};

export default function TermsAndConditions() {
  return (
    <LegalPage title="Terms & Conditions" updated="September 20, 2026">
      <p>
        These Terms &amp; Conditions (&ldquo;Terms&rdquo;) govern your use of the <strong>{BRAND}</strong>{" "}
        automated check-in call, text messages and walking assessment page (together, the &ldquo;Service&rdquo;).
        By taking part in the call, replying to a text or opening your secure link, you agree to these Terms.
      </p>

      <h2>The Service</h2>
      <p>
        {BRAND} places an automated phone call on behalf of your care team, asks a short set of condition-specific
        questions, and &mdash; only if you agree on the call &mdash; texts you a secure link to a page that measures
        your walking using your phone&rsquo;s camera. Results are shared with your care team.
      </p>

      <h2>SMS Terms</h2>
      <ul>
        <li>
          <strong>Program description.</strong> {BRAND} sends a single transactional text message containing a
          secure, time-limited link to your walking assessment page. We do not send marketing or promotional
          messages.
        </li>
        <li>
          <strong>Consent.</strong> You will only receive a text after you say &ldquo;yes&rdquo; when the {BRAND}{" "}
          call asks for permission to text the number the call was placed to. Consent is not a condition of
          receiving care.
        </li>
        <li>
          <strong>Message frequency.</strong> One message per check-in call. If the message is not delivered, your
          care team may resend it once.
        </li>
        <li>
          <strong>Message and data rates may apply.</strong> Standard carrier charges for messaging and data may
          be billed by your mobile provider.
        </li>
        <li>
          <strong>Opt out.</strong> Reply <strong>STOP</strong> to any {BRAND} message to stop receiving texts.
          You can also decline on the call; your survey answers are still saved.
        </li>
        <li>
          <strong>Help.</strong> Reply <strong>HELP</strong> to any {BRAND} message, or contact your care team,
          for assistance.
        </li>
        <li>
          <strong>Carriers.</strong> Carriers are not liable for delayed or undelivered messages.
        </li>
        <li>
          <strong>Privacy.</strong> How we handle your phone number and SMS opt-in data is described in our{" "}
          <Link href="/privacy" className="underline">Privacy Policy</Link>.
        </li>
      </ul>

      <h2>Not medical advice</h2>
      <p>
        The Service collects information for your care team and does not diagnose, treat or replace medical
        advice. If you are in pain, have fallen, feel unwell or have any urgent concern, contact your care team or
        emergency services directly.
      </p>

      <h2>Your responsibilities</h2>
      <ul>
        <li>Answer the call and the walking assessment for yourself only, and keep your secure link private.</li>
        <li>Only perform the walking assessment if you can do so safely, in a clear space, with support nearby if needed.</li>
        <li>Do not attempt to access another patient&rsquo;s information or interfere with the Service.</li>
      </ul>

      <h2>Secure links and access</h2>
      <p>
        Links sent by text expire after a short period and grant access to one patient&rsquo;s page only. Anyone
        who has the link before it expires can open it, so do not forward it. Your care team can issue a new link
        if yours has expired.
      </p>

      <h2>Availability and changes</h2>
      <p>
        The Service is provided &ldquo;as is.&rdquo; Calls, text delivery and the walking page depend on
        telephone networks, carriers and your device, and may be delayed or unavailable. We may update these Terms
        from time to time; the latest version is always available at this page.
      </p>

      <h2>Contact</h2>
      <p>
        For questions about these Terms, contact your care team or reach {BRAND} through the clinician portal.
      </p>
    </LegalPage>
  );
}
