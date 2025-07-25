from __future__ import print_function, division
import numpy as np
import matplotlib.pyplot as plt
import torch
from qutip import *
from torch.utils.data import Dataset

# =====================================================================
# StochasticTwoLevelDataset (Amplitude-Damping version)
# Updated 2025-07-02
# This version incorporates Monte-Carlo amplitude-damping trajectories
# =====================================================================
def normalize(a):
	a_oo = a - np.real(a).min()
	return a_oo/np.abs(a_oo).max()

def get_state(theta, phi):
    ket0, ket1 = np.array([[1.],[0.]]), np.array([[0.],[1.]])
    bloch_state = np.cos(theta/2) * ket0 + np.exp(complex(0, phi))*ket1
    return Qobj(bloch_state)

def get_spherical(theta, phi):
    return np.array([np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi), np.cos(theta)])

def sample_bloch(n_samples=50, rand=True):
    if rand:
        thetas = np.sort(np.pi * np.random.rand(n_samples))
        phis = np.sort(2 * np.pi * np.random.rand(n_samples))
        
    else:
        thetas = np.linspace(0, np.pi, n_samples)
        phis = np.linspace(0, 2 * np.pi, n_samples)
    
    bloch_vec = np.dstack(np.meshgrid(thetas, phis)) # [n_samples, n_samples, 2]
    return bloch_vec.reshape(n_samples * n_samples, 2) # [n_samples^2, 2]

def sample_initial_states(n_samples=50, rand=True):
    " sample initial states "
    bloch_vecs = sample_bloch(n_samples, rand)
    states = [get_state(*bvec) for bvec in bloch_vecs]
    spherical = np.asarray([get_spherical(*bvec) for bvec in bloch_vecs])

    return states, bloch_vecs, spherical

def final_states_to_numpy(states):
    "convert list of quantum objects to numpy array [2, num_time_steps]"
    return np.concatenate([state.full() for state in states], axis=-1)

class StochasticTwoLevelDataset(Dataset):
    """
    Dataset for quantum trajectories with amplitude-damping noise.
    
    This class generates or loads quantum trajectories for a single qubit system,
    with either closed system dynamics or open system dynamics with amplitude damping.
    
    The dataset has shape [num_trajs, time_steps, 3] where:
        - num_trajs: number of trajectories
        - time_steps: number of time points in each trajectory
        - 3: expectation values of [σx, σy, σz]
    
    Each trajectory is normalized so expectation values are within [-1, 1].
    """
    def __init__(self, num_batches=30, batched_samples=6, validation_samples=10, start=0, stop=2, last=10, time_steps=300, mc_samples=250, dataset_type='closed', gamma=0.02, load_from_file=None): 
        """
        Initialize the dataset.
        
        Args:
            num_batches: Number of parameter settings to use
            batched_samples: Number of initial states per parameter set
            validation_samples: Number of validation states
            start, stop, last: Time range and endpoint
            time_steps: Number of time steps in trajectory
            mc_samples: Number of Monte Carlo samples (for open systems)
            dataset_type: 'closed' for unitary evolution, 'open' for amplitude damping
            gamma: Amplitude damping rate (only used if dataset_type='open')
            load_from_file: Optional path to load pre-generated trajectories
        """
        # If loading from file, simply load the data and return
        if load_from_file is not None:
            data = torch.load(load_from_file)
            self.total_expect_data = data['trajectories']
            self.total_time_steps = data['time_steps']
            self.num_trajs = self.total_expect_data.shape[0]
            self.train_time_steps = self.total_time_steps[np.where(self.total_time_steps <= stop)]
            self.train_expect_data = self.total_expect_data[:,:self.train_time_steps.shape[0],:]
            return
            
        self.total_time_steps = np.linspace(start, last, time_steps)
        self.initial_states, _, self.spherical = sample_initial_states(batched_samples, rand=True)
        self.validation_points = sample_initial_states(validation_samples, rand=False)
        self.num_per_batch = batched_samples ** 2
        self.num_batches = num_batches
        self.num_trajs = self.num_per_batch * self.num_batches
        self.dataset_type = dataset_type

        if dataset_type == 'closed':
            self.rand_parameters = np.zeros((num_batches, 2))
        elif dataset_type == 'open':
            self.rand_parameters = np.zeros((num_batches, 3))  # Using gamma for amplitude damping
        expect_data = []
        for i in range(num_batches):
            samp_z = np.random.uniform(1, 2.5, 1)
            samp_x = np.random.uniform(1, 2.5, 1)
            self.rand_parameters[i, 0] = samp_z
            self.rand_parameters[i, 1] = samp_x
            H = samp_z[0] * sigmaz() + samp_x[0] * sigmax()

            if dataset_type == 'closed':
                solve = lambda state : sesolve(H, state, self.total_time_steps, e_ops=[sigmax(), sigmay(), sigmaz()], progress_bar=None)
            elif dataset_type == 'open':
                # Use amplitude damping channel instead of arbitrary noise
                gamma_val = gamma if gamma > 0 else np.random.uniform(0.01, 0.05, 1)[0]
                self.rand_parameters[i, 2] = gamma_val
                
                # Create amplitude damping collapse operator
                c_ops = [np.sqrt(gamma_val) * destroy(2)]  # amplitude damping operator
                
                # Use Monte Carlo to generate quantum trajectories with amplitude damping
                solve = lambda state : mcsolve(H, state, self.total_time_steps, 
                                             c_ops=c_ops, 
                                             e_ops=[sigmax(), sigmay(), sigmaz()],
                                             ntraj=mc_samples,  # Number of trajectories to average
                                             progress_bar=None)
                
            all_states = [solve(state).expect for state in self.initial_states]
            states = [np.asarray(states, dtype='double') for states in all_states] 
            states = np.asarray([np.column_stack([state[0], state[1], state[2]]) for state in states])
            expect_data.append(states)
            
        self.expect_data = np.asarray(expect_data)
        self.total_expect_data = self.expect_data.reshape(self.num_trajs, time_steps, 3)
        self.train_time_steps = self.total_time_steps[np.where(self.total_time_steps <= stop)]
        self.train_expect_data = self.total_expect_data[:,:self.train_time_steps.shape[0],:]

    def plot_trajs(self):
        for i in range(self.num_batches):
            for j in range(self.num_per_batch):
                ts = self.time_steps
                fig, (ax1, ax2, ax3) = plt.subplots(3, 1)

                ax1.plot(ts, self.expect_data[i, j, :, 0])
                ax1.set_ylim(-1, 1)
                ax1.set_ylabel('$\sigma_x$')

                ax2.plot(ts, self.expect_data[i, j, :, 1])
                ax2.set_ylim(-1, 1)
                ax2.set_ylabel('$\sigma_y$')

                ax3.plot(ts, self.expect_data[i, j, :, 2])
                ax3.set_ylim(-1, 1)
                ax3.set_ylabel('$\sigma_z$')
                if self.dataset_type == 'closed':
                    ax3.set_xlabel('H = {}z + {}x'.format(self.rand_parameters[i, 0], self.rand_parameters[i, 1]))
                else:
                    ax3.set_xlabel('H = {}z + {}x decay: {} {}'.format(*self.rand_parameters[i]))

                plt.savefig('plots/stochastic_closed_noise/traj_{}_{}.png'.format(i, j))
                plt.close(fig)

    def render_initial_states(self, directory):
        bloch = Bloch()
        colors = normalize(self.spherical)
        bloch.point_color = colors
        bloch.add_points([self.spherical[:, 0], self.spherical[:, 1], self.spherical[:, 2]], 'm')
        bloch.save(directory)

# two qubit functions

def random_u(N):
    #Return a Haar distributed random unitary NxN
    #N being the system dimension
    Z = np.random.randn(N,N) + 1.0j * np.random.randn(N,N)
    [Q,R] = np.linalg.qr(Z)    # QR decomposition
    D = np.diag(np.diagonal(R) / np.abs(np.diagonal(R)))
    return np.dot(Q, D)

def random_psi():
    #Return random state, within computational subspace {|0>,|1>} 
    Ur = random_u(2)
    alpha = Ur[0,0]
    beta = Ur[1,0]
    ket0, ket1 = np.array([[1.],[0.]]), np.array([[0.],[1.]])
    rand_vector = alpha * ket0 + beta * ket1 # alpha |0> + beta |1>
    return alpha, beta, rand_vector

def two_qubit_initial(num):
    """
    Create a list of random two-qubit initial states with proper QuTiP tensor structure.
    
    Args:
        num: Number of initial states to generate
        
    Returns:
        List of random two-qubit states as QuTiP Qobj with dims=[[2,2],[1,1]]
    """
    initial_states = []
    for i in range(num):
        alpha1, beta1, vec1 = random_psi()
        alpha2, beta2, vec2 = random_psi()
        
        # Create the tensor product manually to ensure correct dimensions
        # Tensor product of |ψ1⟩ ⊗ |ψ2⟩ is a vector of length 4
        tensor_vec = np.kron(vec1, vec2)  # Direct Kronecker product of vectors
        
        # Create a Qobj with explicitly specified dimensions
        two_qubit_state = Qobj(tensor_vec, dims=[[2, 2], [1, 1]])
        
        # Add the state to our list
        initial_states.append(two_qubit_state)
    
    return initial_states


class TwoQubitDataset(Dataset):
    def __init__(self, omega=1, delta=1, J=1, num_batches=30, num_trajs=36, time_steps=300, stop=2, end=10):
        """
        Two-qubit dataset generator with proper tensor product dimensions.
        
        Args:
            omega: Energy level splitting parameter
            delta: Transverse field parameter
            J: Coupling strength
            num_batches: Number of parameter settings to use
            num_trajs: Number of trajectories per batch
            time_steps: Number of time steps
            stop: Stop time for training subset
            end: End time for full dataset
        """
        # Create the basis operators for a single qubit
        sx = sigmax()
        sy = sigmay()
        sz = sigmaz()
        id = qeye(2)
        
        # Create the tensor product operators with correct dimensions
        sigmax1 = tensor(sx, id)  # sigmax ⊗ I
        sigmax2 = tensor(id, sx)  # I ⊗ sigmax
        sigmaz1 = tensor(sz, id)  # sigmaz ⊗ I
        sigmaz2 = tensor(id, sz)  # I ⊗ sigmaz
        
        # Verify dimensions for operators
        for op in [sigmax1, sigmax2, sigmaz1, sigmaz2]:
            assert op.dims == [[2, 2], [2, 2]], f"Operator has incorrect dimensions: {op.dims}"
        
        self.num_trajs = num_batches * num_trajs
        self.initial_states = two_qubit_initial(num_trajs)
        self.total_time_steps = np.linspace(0, end, time_steps)
        
        # Store operators for solver
        self.e_ops = [sigmax1, sigmax2, sigmaz1, sigmaz2]
        
        expect_data = []
        # Verify state dimensions (QuTiP's default tensor dims are [[2,2], [1]])
        assert self.initial_states[0].dims == [[2, 2], [1]], \
            f"Initial state has incorrect dimensions: {self.initial_states[0].dims}"
            
        # Validate tensor product operations
        test_op = tensor(sx, sx)
        assert test_op.dims == [[2, 2], [2, 2]], f"Tensor product operation has incorrect dimensions: {test_op.dims}"
        
        for i in range(num_batches):
            samp_z = np.random.uniform(1, 2.5, 1)[0]
            samp_x = np.random.uniform(1, 2.5, 1)[0]
            
            # Construct Hamiltonian with proper dimensions using QuTiP operations
            H = (omega / 2 * samp_z * sigmaz1) + \
                (delta / 2 * samp_x * sigmax1) + \
                (omega / 2 * samp_z * sigmaz2) + \
                (delta / 2 * samp_x * sigmax2) + \
                (J * tensor(sx, sx))  # Proper way to represent the coupling term
            
            # Verify Hamiltonian dimensions
            assert H.dims == [[2, 2], [2, 2]], f"Hamiltonian has incorrect dimensions: {H.dims}"
            
            # Ensure the Hamiltonian is Hermitian
            assert H.isherm, "Hamiltonian is not Hermitian!"
            
            # QuTiP automatically handles the dimension compatibility between operators and states
            solve = lambda state : sesolve(H, state, self.total_time_steps, 
                                        e_ops=self.e_ops, progress_bar=None)
            all_states = [solve(state).expect for state in self.initial_states]
            states = [np.asarray(states, dtype='double') for states in all_states] 
            states = np.asarray([np.column_stack([state[0], state[1], state[2], state[3]]) for state in states])
            expect_data.append(states)
        
        expect_data = np.asarray(expect_data)
        self.total_expect_data = expect_data.reshape(self.num_trajs, time_steps, 4)
        self.train_time_steps = self.total_time_steps[np.where(self.total_time_steps <= stop)]
        self.train_expect_data = self.total_expect_data[:,:self.train_time_steps.shape[0],:]

if __name__ == '__main__':
    # Example usage of the datasets
    print("Creating single qubit dataset with amplitude damping...")
    data = StochasticTwoLevelDataset(num_batches=5, batched_samples=4, 
                                    time_steps=200, dataset_type='open', 
                                    gamma=0.02, mc_samples=100)
    print(f"Single qubit dataset shape: {data.total_expect_data.shape}")
    
    # Save the dataset
    save_dict = {
        'trajectories': data.total_expect_data,
        'time_steps': data.total_time_steps,
        'parameters': data.rand_parameters
    }
    torch.save(save_dict, 'saved_datasets/ad_1q_monte_carlo_gamma0.02.pt')
    print("Dataset saved to saved_datasets/ad_1q_monte_carlo_gamma0.02.pt")
    
    # Two-qubit example
    print("\nCreating two-qubit dataset...")
    two_qubit_data = TwoQubitDataset(num_batches=3, num_trajs=10)
    print(f"Two-qubit dataset shape: {two_qubit_data.total_expect_data.shape}")
