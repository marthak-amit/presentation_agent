import { Link, Route, Routes } from "react-router-dom";
import Upload from "./pages/Upload";
import Presenter from "./pages/Presenter";
import Debug from "./pages/Debug";
import Check from "./pages/Check";

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          PresenterAgent
        </Link>
        <nav>
          <Link to="/">Upload</Link>
          <Link to="/check">Check setup</Link>
          <Link to="/debug">Debug</Link>
        </nav>
      </header>
      <Routes>
        <Route path="/" element={<Upload />} />
        <Route path="/present/:deckId" element={<Presenter />} />
        <Route path="/check" element={<Check />} />
        <Route path="/debug" element={<Debug />} />
        <Route path="/debug/:deckId" element={<Debug />} />
      </Routes>
    </div>
  );
}
