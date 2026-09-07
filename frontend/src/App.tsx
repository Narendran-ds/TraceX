/**
 * Two states: intake, and the workspace. A trace is one task, so it does not
 * need a router — and one fewer dependency is one fewer thing to break offline.
 */

import { Intake } from './screens/Intake';
import { Workspace } from './screens/Workspace';
import { useCase } from './lib/useCase';

export default function App() {
  const investigation = useCase();

  // A failure before the case even opened belongs back on intake with the
  // reason attached — dropping the investigator into an empty workspace would
  // make a readable error look like a broken screen.
  const openedCase = investigation.data.summary !== null;
  const started = investigation.phase !== 'idle' && openedCase;

  return (
    <div className="h-full">
      {started ? (
        <Workspace
          data={investigation.data}
          phase={investigation.phase}
          events={investigation.events}
          liveCounts={investigation.liveCounts}
          percent={investigation.percent}
          elapsedMs={investigation.elapsedMs}
          error={investigation.error}
          onReview={investigation.review}
          onGenerateReport={investigation.generateReport}
          onVerify={investigation.verify}
          onNewCase={investigation.reset}
        />
      ) : (
        <Intake
          onStart={investigation.start}
          busy={investigation.phase === 'creating'}
          error={investigation.error}
        />
      )}
    </div>
  );
}
