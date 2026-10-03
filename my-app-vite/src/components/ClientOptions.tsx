type ClientChoice = {
  id: number;
  name?: string | null;
  login?: string;
  workStatus?: string | null;
};

export default function ClientOptions({ clients, showIds = true }: { clients: ClientChoice[]; showIds?: boolean }) {
  const active = clients.filter((client) => client.workStatus !== 'Неактивен');
  const inactive = clients.filter((client) => client.workStatus === 'Неактивен');
  const options = (items: ClientChoice[]) => items.map((client) => (
    <option key={client.id} value={client.id}>
      {client.name || client.login}{showIds ? ` (id: ${client.id})` : ''}
    </option>
  ));

  if (!inactive.length) return <>{options(active)}</>;
  return <>
    {active.length > 0 && <optgroup label="Активные">{options(active)}</optgroup>}
    <optgroup label="Неактивные">{options(inactive)}</optgroup>
  </>;
}
