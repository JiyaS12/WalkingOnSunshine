import type { Metadata } from "next";
import Link from "next/link";
import { BRAND, LegalPage } from "@/app/components/LegalPage";

export const metadata: Metadata = {
  title: `Privacy Policy | ${BRAND}`,
  description: `How ${BRAND} collects, uses and protects patient information, including SMS opt-in data.`,
};

export default function PrivacyPolicy() {
  return (
    <LegalPage title="Privacy Policy" updated="September 20, 2026">
      <p>
        This Privacy Policy describes how <strong>{BRAND}</strong> (&ldquo;{BRAND},&rdquo; &ldquo;we,&rdquo;
        &ldquo;us&rdquo;) collects, uses and protects information when a patient&rsquo;s care team uses {BRAND} to
        run an automated post-operative check-in call and a short walking assessment.
      </p>

      <h2>Information we collect</h2>
      <ul>
        <li>
          <strong>Contact details</strong> &mdash; the phone number your care team enters so {BRAND} can place the
          check-in call and, with your spoken permission, send you one text message containing your secure link.
        </li>
        <li>
          <strong>Check-in answers</strong> &mdash; your confirmed responses to the condition-specific survey questions
          asked on the call (for example, the HOOS JR hip questions or the stroke mobility questions), and a text
          transcript of the call. We do not keep recordings of your voice.
        </li>
        <li>
          <strong>Walking assessment data</strong> &mdash; when you open your secure link and start the camera, the
          page measures your walking pattern in your browser and saves the resulting metrics (such as cadence,
          step timing and balance scores) and a stick-figure replay. Video is processed on your device and is not
          uploaded or stored.
        </li>
        <li>
          <strong>Technical data</strong> &mdash; message delivery status from our telephony provider, timestamps
          and error codes needed to run the service reliably.
        </li>
      </ul>

      <h2>How we use it</h2>
      <ul>
        <li>To place the check-in call and read you the survey questions.</li>
        <li>To text you a single, time-limited link to your walking assessment page after you say yes on the call.</li>
        <li>To make your answers and walking results available to your care team in the clinician portal.</li>
        <li>To keep the service secure, diagnose delivery problems and prevent misuse.</li>
      </ul>

      <h2>SMS opt-in data</h2>
      <p>
        {BRAND} only sends a text message after you have given verbal consent during the call. The message contains
        your secure link and nothing else; we do not send marketing or promotional texts. Your consent, phone number
        and message delivery status are recorded so your care team can see whether the link reached you.
      </p>
      <p>
        <strong>
          We do not sell or share your SMS opt-in data or personal information with third parties for marketing
          purposes.
        </strong>
      </p>

      <h2>Who can see your information</h2>
      <ul>
        <li>Your care team, through a login-protected clinician portal.</li>
        <li>
          Service providers that operate the phone call, speech recognition, speech synthesis and text delivery on
          our behalf, only to the extent needed to provide the service.
        </li>
        <li>Authorities when required by law.</li>
      </ul>

      <h2>Security and retention</h2>
      <p>
        Links sent by text are signed and expire after a short period (15 minutes by default) and grant access to
        one patient&rsquo;s page only. Clinician access requires a login. We keep check-in answers and walking
        results for as long as your care team needs them for your care, and delete or de-identify them when they
        are no longer required.
      </p>

      <h2>Your choices</h2>
      <ul>
        <li>Say &ldquo;no&rdquo; when the call asks permission to text you; your answers are still saved and no text is sent.</li>
        <li>Say &ldquo;stop&rdquo; at any point during the call to end it.</li>
        <li>Reply <strong>STOP</strong> to any text from {BRAND} to opt out of further messages, or <strong>HELP</strong> for help.</li>
        <li>Ask your care team to review, correct or delete the information {BRAND} holds about you.</li>
      </ul>

      <h2>Contact</h2>
      <p>
        Questions about this policy can be directed to your care team or to {BRAND} through the clinician portal.
        See also our <Link href="/terms" className="underline">Terms &amp; Conditions</Link>.
      </p>
    </LegalPage>
  );
}
