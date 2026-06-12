using UnityEngine;

namespace Sample.Game
{
    /// <summary>Chases the player and fires when in range. Mirrors the
    /// MonoBehaviour shape found in real Unity projects.</summary>
    public class EnemyAI : MonoBehaviour
    {
        [SerializeField] private Transform player;
        [SerializeField] private float speed = 3.5f;
        [SerializeField] private float fireRange = 8f;

        private static readonly int FIRE_HASH = Animator.StringToHash("Fire");

        public bool IsAlerted { get; private set; }

        private void Start()
        {
            player = GameObject.FindWithTag("Player").transform;
        }

        private void Update()
        {
            if (player == null) return;
            transform.position = Vector3.MoveTowards(
                transform.position, player.position, speed * Time.deltaTime);
            if (Vector3.Distance(transform.position, player.position) < fireRange)
            {
                Fire();
            }
        }

        /// <summary>Spawns a projectile aimed at the player.</summary>
        private void Fire()
        {
            var projectile = new GameObject("projectile");
            GetComponent<Animator>().SetTrigger(FIRE_HASH);
        }

        public void Alert() => IsAlerted = true;
    }
}
