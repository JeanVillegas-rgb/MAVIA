import { Link } from "react-router-dom";
export default function VerifyEmailPage() {
  return <main className="mv-auth__card"><h1>Email verification</h1>
    <p>Email verification is not enabled on this backend. After registration, you can log in directly.</p>
    <Link to="/login">Go to login</Link></main>;
}
